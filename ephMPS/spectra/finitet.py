# -*- coding: utf-8 -*-
# Author: Jiajun Ren <jiajunren0522@gmail.com>
import numpy as np

from ephMPS.mps import Mpo, Mps, MpDm
from ephMPS.spectra.base import SpectraTdMpsJobBase, BraKetPair
from ephMPS.utils import constant


class BraKetPairEmiFiniteT(BraKetPair):

    def calc_ft(self):
        return np.conj(super(BraKetPairEmiFiniteT, self).calc_ft())


class BraKetPairAbsFiniteT(BraKetPair): pass


class SpectraFiniteT(SpectraTdMpsJobBase):

    def __init__(self, mol_list, spectratype, temperature, insteps, offset, gs_shift=0):
        self._exact_eiht_cache = {}
        self.temperature = temperature
        self.insteps = insteps
        self.gs_shift = gs_shift
        super(SpectraFiniteT, self).__init__(mol_list, spectratype, temperature, offset=offset)

    def init_mps(self):
        if self.spectratype == "emi":
            return self.init_mps_emi()
        else:
            return self.init_mps_abs()

    def init_mps_emi(self):
        dipole_mpo = Mpo.onsite(self.mol_list, 'a', dipole=True)
        i_mpo = MpDm.max_entangled_ex(self.mol_list)
        ket_mpo = i_mpo.thermal_prop(self.h_mpo, self.insteps, temperature=self.temperature)
        # e^{\-beta H/2} \Psi
        ket_mpo.normalize()
        dipole_mpo_dagger = dipole_mpo.conj_trans()
        dipole_mpo_dagger.build_empty_qn()
        a_ket_mpo = ket_mpo.apply(dipole_mpo_dagger)
        a_bra_mpo = a_ket_mpo.copy()
        return BraKetPairEmiFiniteT(a_bra_mpo, a_ket_mpo)

    def init_mps_abs(self):
        dipole_mpo = Mpo.onsite(self.mol_list, 'a^\dagger', dipole=True)
        gs_mps = Mps.gs(self.mol_list, max_entangled=True)
        i_mpo = MpDm.from_mps(gs_mps)
        beta = constant.t2beta(self.temperature)
        thermal_mpo = Mpo.exact_propagator(self.mol_list, -beta / 2.0, shift=self.gs_shift)
        ket_mpo = thermal_mpo.apply(i_mpo)
        ket_mpo.normalize()
        # e^{\-beta H/2} \Psi
        a_ket_mpo = dipole_mpo.apply(ket_mpo)
        a_bra_mpo = a_ket_mpo.copy()
        return BraKetPairAbsFiniteT(a_bra_mpo, a_ket_mpo)

    def exact_eiht(self, evolve_dt):
        if evolve_dt not in self._exact_eiht_cache:
            self._exact_eiht_cache[evolve_dt] = Mpo.exact_propagator(self.mol_list, 1.0j * evolve_dt, shift=self.gs_shift)
        return self._exact_eiht_cache[evolve_dt]

    def exact_eihpt(self, evolve_dt):
        return self.exact_eiht(-evolve_dt)

    def exact_eihmt(self, evolve_dt):
        return self.exact_eiht(evolve_dt)

    def evolve_single_step(self, evolve_dt):
        latest_bra_mpo, latest_ket_mpo = self.latest_mps
        if len(self.tdmps_list) % 2 == 1:
            latest_ket_mpo = latest_ket_mpo.apply(self.exact_eihmt(evolve_dt)).evolve(self.h_mpo, evolve_dt)
        else:
            latest_bra_mpo = latest_bra_mpo.apply(self.exact_eihpt(evolve_dt)).evolve(self.h_mpo, -evolve_dt)
        return self.latest_mps.__class__(latest_bra_mpo, latest_ket_mpo)