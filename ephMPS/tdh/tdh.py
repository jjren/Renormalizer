# -*- coding: utf-8 -*-
# Author:j_matrixiajun Ren <jiajunren0522@gmail.com>

'''
Time dependent Hartree (TDH) solver for vibronic coupling problem
'''
import logging
import copy

import scipy
import numpy as np

from ephMPS.tdh import mflib
import ephMPS.mps.rk
from ephMPS.utils.tdmps import TdMpsJob
from ephMPS.utils import constant

logger = logging.getLogger(__name__)


def SCF(mol_list, nexciton, niterations=20, thresh=1e-5, particle="hardcore boson"):
    '''
    1. SCF includes both the electronic and vibrational parts
    2. if electronic part is Fermion, the electronic part is the same as HF orbital
    each electron has 1 orbital, but if electronic part is hardcore boson, only
    one many-body wfn is used for electronic DOF
    '''
    assert mol_list.pure_hartree
    assert particle in ["hardcore boson", "fermion"]

    # initial guess
    WFN = []
    fe = 0
    fv = 0

    # electronic part
    H_el_indep, H_el_dep = Ham_elec(mol_list, nexciton, particle=particle)
    ew, ev = scipy.linalg.eigh(a=H_el_indep)
    if particle == "hardcore boson":
        WFN.append(ev[:, 0])
        fe += 1
    elif particle == "fermion":
        # for the fermion, maybe we can directly use one particle density matrix for
        # both zero and finite temperature
        if nexciton == 0:
            WFN.append(ev[:, 0])
            fe += 1
        else:
            for iexciton in range(nexciton):
                WFN.append(ev[:, iexciton])
                fe += 1
    else:
        assert False

    # vibrational part
    for mol in mol_list:
        for iph in range(mol.nphs):
            vw, vv = scipy.linalg.eigh(a=mol.hartree_phs[iph].h_indep)
            WFN.append(vv[:, 0])
            fv += 1

    # append the coefficient a
    WFN.append(1.0)

    Etot = None
    for itera in range(niterations):
        logger.info("Loop: %s" % itera)

        # mean field Hamiltonian and energy
        HAM, Etot = construct_H_Ham(mol_list, nexciton, WFN, fe, fv, particle=particle)
        logger.info("Etot= %g" % Etot)


        WFN_old = WFN
        WFN = []
        for iham, ham in enumerate(HAM):
            w, v = scipy.linalg.eigh(a=ham)
            if iham < fe:
                WFN.append(v[:, iham])
            else:
                WFN.append(v[:, 0])

        WFN.append(1.0)

        # density matrix residual
        res = [scipy.linalg.norm(np.tensordot(WFN[iwfn], WFN[iwfn], axes=0) \
                                 - np.tensordot(WFN_old[iwfn], WFN_old[iwfn], axes=0)) for iwfn in range(len(WFN) - 1)]
        if np.all(np.array(res) < thresh):
            logger.info("SCF converge!")
            break

    return WFN, Etot


def unitary_propagation(HAM, WFN, dt):
    '''
    unitary propagation e^-iHdt * wfn(dm)
    '''
    ndim = WFN[0].ndim
    for iham, ham in enumerate(HAM):
        w, v = scipy.linalg.eigh(ham)
        if ndim == 1:
            WFN[iham] = v.dot(np.exp(-1.0j * w * dt) * v.T.dot(WFN[iham]))
        elif ndim == 2:
            WFN[iham] = v.dot(np.diag(np.exp(-1.0j * w * dt)).dot(v.T.dot(WFN[iham])))
            # print iham, "norm", scipy.linalg.norm(WFN[iham])
        else:
            assert False


def Ham_elec(mol_list, nexciton, indirect=None, particle="hardcore boson"):
    '''
    construct electronic part Hamiltonian
    '''

    assert particle in ["hardcore boson", "fermion"]

    nmols = len(mol_list)
    if nexciton == 0:  # 0 exciton space
        # independent part
        H_el_indep = np.zeros([1, 1])
        # dependent part, for Holstein model a_i^\dagger a_i
        H_el_dep = [np.zeros([1, 1])] * nmols

    elif nexciton == 1 or particle == "fermion":
        H_el_indep = np.zeros((nmols, nmols))
        for imol, mol in enumerate(mol_list):
            for jmol in range(nmols):
                if imol ==jmol:
                    H_el_indep[imol, imol] = mol.elocalex + mol.hartree_e0
                else:
                    H_el_indep[imol,jmol] = mol_list.j_matrix[imol,jmol]

        H_el_dep = []
        # a^dagger_imol a_imol
        for imol in range(nmols):
            tmp = np.zeros((nmols, nmols))
            tmp[imol, imol] = 1.0
            H_el_dep.append(tmp)
    else:
        raise NotImplementedError
        # todo: hardcore boson and nexciton > 1, construct the full Hamiltonian
        # if indirect is not None:
        #    x, y = indirect
        # nconfigs = x[-1,-1]
        # H_el_indep = np.zeros(nconfigs, nconfigs)
        # H_el_dep = np.zeros(nconfigs, nconfigs)
        # for idx in range(nconfigs):
        #    iconfig = configidx.idx2exconfig(idx, x)
        #    for imol in range(nmols):
        #        if iconfig[imol] == 1:
        #            # diagonal part
        #            H_el_indep[idx, idx] += mol[imol].elocalex + mol[imol].e0
        #            #H_el_dep[idx, idx] =
        #
        #            # non-diagonal part
        #            forj_matrixmol in range(nmols):
        #                if iconfig[jmol] == 0:
        #                    iconfigbra = copy.deepcopy(iconfig)
        #                    iconfigbra[jmol] = 1
        #                    iconfigbra[imol] = 0
        #                    idxbra = configidx.exconfig2idx(iconfigbra, y)
        #                    if idxbra is not None:
        #                        H_el_indep[idxbra,idx] =j_matrix[jmol, imol]

    return H_el_indep, H_el_dep


def construct_H_Ham(mol_list, nexciton, WFN, fe, fv, particle="hardcore boson", debug=False):
    '''
    construct the mean field Hartree Hamiltonian
    the many body terms are A*B, A(B) is the electronic(vibrational) part mean field
    '''
    assert particle in ["hardcore boson", "fermion"]
    assert fe + fv == len(WFN) - 1

    nmols = len(mol_list)

    A_el = np.zeros((nmols, fe))
    H_el_indep, H_el_dep = Ham_elec(mol_list, nexciton, particle=particle)

    for ife in range(fe):
        A_el[:, ife] = np.array([mflib.exp_value(WFN[ife], iH_el_dep, WFN[ife]) for iH_el_dep in H_el_dep]).real
        if debug:
            logger.debug(str(ife) + " state electronic occupation " + str(A_el[:, ife]))
            

    B_vib = []
    iwfn = fe
    for mol in mol_list:
        B_vib.append([])
        for ph in mol.hartree_phs:
            B_vib[-1].append(mflib.exp_value(WFN[iwfn], ph.h_dep, WFN[iwfn]))
            iwfn += 1
    B_vib_mol = [np.sum(np.array(i)) for i in B_vib]

    Etot = 0.0
    HAM = []
    for ife in range(fe):
        # the mean field energy of ife state
        e_mean = mflib.exp_value(WFN[ife], H_el_indep, WFN[ife]) + A_el[:, ife].dot(B_vib_mol)
        ham = H_el_indep - np.diag([e_mean] * H_el_indep.shape[0])
        for imol in range(nmols):
            ham += H_el_dep[imol] * B_vib_mol[imol]
        HAM.append(ham)
        Etot += e_mean

    iwfn = fe
    for imol, mol in enumerate(mol_list):
        for iph in range(mol.nphs):
            h_indep = mol.hartree_phs[iph].h_indep
            h_dep = mol.hartree_phs[iph].h_dep
            e_mean = mflib.exp_value(WFN[iwfn], h_indep, WFN[iwfn])
            Etot += e_mean  # no double counting of e-ph coupling energy
            e_mean += np.sum(A_el[imol, :]) * B_vib[imol][iph]
            HAM.append(h_indep + h_dep * np.sum(A_el[imol, :]) - np.diag([e_mean] * WFN[iwfn].shape[0]))
            iwfn += 1

    if debug:
        return HAM, Etot, A_el
    else:
        return HAM, Etot


class TdHartree(TdMpsJob):

    def __init__(self, mol_list, nexciton, particle, prop_method, temperature=0, insteps=None):
        assert mol_list.pure_hartree
        self.mol_list = mol_list
        self.nexciton = nexciton
        assert particle in ["hardcore boson", "fermion"]
        self.particle = particle
        self.prop_method = prop_method
        self.temperature = temperature
        self.insteps = insteps
        self.fe = self.fv = 0
        if particle == "hardcore boson":
            self.fe += 1
        elif particle == "fermion":
            raise NotImplementedError
        for mol in self.mol_list:
            self.fv += len(mol.hartree_phs)

        super(TdHartree, self).__init__()

    def init_mps(self):
        raise NotImplementedError

    def construct_H_Ham(self, nexciton, WFN, debug=False):
        return construct_H_Ham(self.mol_list, nexciton, WFN, self.fe, self.fv, particle=self.particle, debug=debug)

    def checkWfn(self, WFN):
        assert (self.fe + self.fv) == len(WFN) - 1

    def evolve_single_step(self, evolve_dt):
        raise NotImplementedError

    def _evolve_single_step(self, evolve_dt, WFN, nexciton):
        f = self.fe + self.fv
        self.checkWfn(WFN)
        WFN = WFN.copy()
        # EOM of wfn
        if self.prop_method == "unitary":
            HAM, Etot = self.construct_H_Ham(nexciton, WFN)
            unitary_propagation(HAM, WFN, evolve_dt)
        else:
            rk = ephMPS.mps.rk.Runge_Kutta(method=self.prop_method)
            RK_a, RK_b, RK_c = rk.tableau

            klist = []
            for istage in range(rk.stage):
                WFN_temp = copy.deepcopy(WFN)
                for jterm in range(istage):
                    for iwfn in range(f):
                        WFN_temp[iwfn] += klist[jterm][iwfn] * RK_a[istage][jterm] * evolve_dt
                HAM, Etot_check = self.construct_H_Ham(nexciton, WFN_temp)
                if istage == 0:
                    Etot = Etot_check

                klist.append([HAM[iwfn].dot(WFN_temp[iwfn]) / 1.0j for iwfn in range(f)])

            for iwfn in range(f):
                for istage in range(rk.stage):
                    WFN[iwfn] += RK_b[istage] * klist[istage][iwfn] * evolve_dt

        # EOM of coefficient a
        logger.info("Etot %g" % Etot)
        WFN[-1] *= np.exp(Etot / 1.0j * evolve_dt)
        self.checkWfn(WFN)
        return WFN

    def _FT_DM(self, nexciton):
        '''
        finite temperature thermal equilibrium density matrix by imaginary time TDH
        '''

        DM = []

        # initial state infinite T density matrix
        H_el_indep, H_el_dep = Ham_elec(self.mol_list, nexciton, particle=self.particle)
        dim = H_el_indep.shape[0]
        DM.append(np.diag([1.0] * dim, k=0))


        for mol in self.mol_list:
            for ph in mol.hartree_phs:
                dim = ph.h_indep.shape[0]
                DM.append(np.diag([1.0] * dim, k=0))

        # the coefficent a
        DM.append(1.0)

        # normalize the dm (physical \otimes ancilla)
        mflib.normalize(DM)

        beta = constant.t2beta(self.temperature) / 2.0
        dbeta = beta / float(self.insteps)

        for istep in range(self.insteps):
            DM = self._evolve_single_step(dbeta / 1.0j, DM, nexciton)
            mflib.normalize(DM)

        Z = DM[-1] ** 2
        logger.info("partition function Z=%g" % Z)

        # divide by np.sqrt(partition function)
        DM[-1] = 1.0
        return DM

    def get_dump_dict(self):
        raise NotImplementedError


class LinearSpectra(TdHartree):

    def __init__(self, spectratype, mol_list, particle="hardcore boson", prop_method="unitary", E_offset=0.,
                 temperature=0, insteps=None):
        assert spectratype in ["abs", "emi"]
        self.spectratype = spectratype
        self.E_offset = E_offset
        if self.spectratype == "abs":
            self.dipolemat = construct_onsiteO(mol_list, "a^\dagger", dipole=True)
            nexciton = 1
        elif self.spectratype == "emi":
            self.dipolemat = construct_onsiteO(mol_list, "a", dipole=True)
            nexciton = 0
        else:
            assert False
        self.autocorr = []
        super(LinearSpectra, self).__init__(mol_list, nexciton, particle, prop_method, temperature, insteps)

    # check whether the energy is conserved
    def check_conserveE(self, WFNbra, WFNket):
        if self.temperature == 0:
            Nouse, Etot_bra = self.construct_H_Ham(self.nexciton, WFNbra)
        else:
            Nouse, Etot_bra = self.construct_H_Ham(1 - self.nexciton, WFNbra)

        Nouse, Etot_ket = self.construct_H_Ham(self.nexciton, WFNket)

        return Etot_bra, Etot_ket

    def calc_autocorr(self, WFNbra, WFNket):
        # E_offset to add a prefactor
        autocurr = np.conj(WFNbra[-1]) * WFNket[-1] * np.exp(-1.0j * self.E_offset * self.latest_evolve_time)
        for iwfn in range(self.fe + self.fv):
            if self.temperature == 0:
                autocurr *= np.vdot(WFNbra[iwfn], WFNket[iwfn])
            else:
                # FT
                if iwfn == 0:
                    autocurr *= mflib.exp_value(WFNbra[iwfn], self.dipolemat.T, WFNket[iwfn])
                else:
                    autocurr *= np.vdot(WFNbra[iwfn], WFNket[iwfn])

        self.autocorr.append(autocurr)

    def init_zt(self):

        if self.spectratype == "abs":
            WFN, Etot = SCF(self.mol_list, 0)
        elif self.spectratype == "emi":
            WFN, Etot = SCF(self.mol_list, 1)
        else:
            assert False

        return  [wfn.astype(np.complex128) for wfn in WFN[:-1]] + [complex(WFN[-1])]

    def init_ft(self):
        if self.spectratype == 'abs':
            DM = self._FT_DM(0)
        elif self.spectratype == 'emi':
            DM = self._FT_DM(1)
        else:
            assert False
        return DM

    def init_mps(self):
        if self.temperature == 0:
            WFN =  self.init_zt()
        else:
            WFN = self.init_ft()

        WFNket = copy.deepcopy(WFN)
        WFNket[0] = self.dipolemat.dot(WFNket[0])

        # normalize ket
        mflib.normalize(WFNket)

        if self.temperature == 0:
            WFNbra = copy.deepcopy(WFNket)
        else:
            WFNbra = copy.deepcopy(WFN)

        # normalize bra
        mflib.normalize(WFNbra)

        self.calc_autocorr(WFNbra, WFNket)

        return WFNbra, WFNket


    def evolve_single_step(self, evolve_dt):
        WFNbra, WFNket = self.latest_mps
        if self.temperature == 0:
            if len(self.autocorr) % 2 == 1:
                WFNket = self._evolve_single_step(evolve_dt, WFNket, self.nexciton)
            else:
                WFNbra = self._evolve_single_step(-evolve_dt, WFNbra, self.nexciton)
        else:
            # FT
            WFNket = self._evolve_single_step(evolve_dt, WFNket, self.nexciton)
            WFNbra = self._evolve_single_step(evolve_dt, WFNbra, 1 - self.nexciton)

        self.calc_autocorr(WFNbra, WFNket)
        return WFNbra, WFNket


class Dynamics(TdHartree):
    
    def __init__(self, mol_list, property_ops, temperature=0, insteps=None):
        self.property_ops = property_ops
        self.properties = [[] for _ in property_ops]
        super(Dynamics, self).__init__(mol_list, 1, "hardcore boson", "unitary", temperature, insteps)
        self.update_properties(self.latest_mps)

    def init_zt(self):
        WFN, Etot = SCF(self.mol_list, 0)
        dipoleO = construct_onsiteO(self.mol_list, "a^\dagger", dipole=True, mol_idx_set={0})
        WFN[0] = dipoleO.dot(WFN[0])
        mflib.normalize(WFN)
        return WFN

    def init_ft(self):
        '''
        finite temperature thermal equilibrium density matrix by imaginary time TDH
        '''
        DM = self._FT_DM(0)

        dipoleO = construct_onsiteO(self.mol_list, "a^\dagger", dipole=True, mol_idx_set={0})
        DM[0] = dipoleO.dot(DM[0])
        mflib.normalize(DM)

        return DM

    def init_mps(self):
        if self.temperature == 0:
            return self.init_zt()
        else:
            return self.init_ft()


    def update_properties(self, WFN):
        # calculate the expectation value
        for iO, O in enumerate(self.property_ops):
            ft = mflib.exp_value(WFN[0], O, WFN[0])
            ft *= np.conj(WFN[-1]) * WFN[-1]
            self.properties[iO].append(ft)
    
    def evolve_single_step(self, evolve_dt):
        WFN = self._evolve_single_step(evolve_dt, self.latest_mps, 1)
        self.update_properties(WFN)
        return WFN

def construct_intersiteO(mol, idxmol,j_matrixdxmol):
    '''
    construct the electronic inter site operator \sum_i a_i^\dagger a_j
    '''
    pass

def construct_onsiteO(mol_list, opera, dipole=False, mol_idx_set=None):
    '''
    construct the electronic onsite operator \sum_i opera_i MPO
    '''
    assert mol_list.pure_hartree
    assert opera in ["a", "a^\dagger", "a^\dagger a"]
    nmols = len(mol_list)
    if mol_idx_set is None:
        mol_idx_set = set(np.arange(nmols))

    element = np.zeros(nmols)
    for site in mol_idx_set:
        if dipole == False:
            element[site] = 1.0
        else:
            element[site] = mol_list[site].dipole

    if opera == "a":
        O = np.zeros([1, nmols])
        O[0, :] = element
    elif opera == "a^\dagger":
        O = np.zeros([nmols, 1])
        O[:, 0] = element
    elif opera == "a^\dagger a":
        O = np.diag(element)
    else:
        assert False

    return O