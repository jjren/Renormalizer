# -*- coding: utf-8 -*-
# Author: Jiajun Ren <jiajunren0522@gmail.com>

import unittest

import numpy as np
from ddt import ddt, data, unpack

from ephMPS.tdh import tdh
from ephMPS.tests.parameter import hartree_mol_list, custom_mol_list, ph_phys_dim
from ephMPS.utils import Quantity, constant


@ddt
class Test_FT_TDH(unittest.TestCase):

    def test_FT_DM(self):
        # TDH
        nexciton = 1
        T = Quantity(298, "K")
        insteps = 100
        tdHartree = tdh.Dynamics(hartree_mol_list, property_ops=[], temperature=T, insteps=insteps)
        DM = tdHartree._FT_DM(nexciton)
        HAM, Etot, A_el = tdHartree.construct_H_Ham(nexciton, DM, debug=True)
        self.assertAlmostEqual(Etot, 0.0856330141528)
        occ_std = np.array([[0.20300487], [0.35305247],[0.44394266]])
        self.assertTrue(np.allclose(A_el, occ_std))                
        
        # DMRGresult
        # energy = 0.08534143842580197
        # occ = 0.20881751295568823, 0.35239681740226808, 0.43878566964204374

    @data(
            [[0.0, 0.0],"emi","std_data/TDH/TDH_FT_emi_0.npy"],
            [[30.1370, 8.7729],"emi","std_data/TDH/TDH_FT_emi.npy"],
            [[0.0, 0.0],"abs","std_data/TDH/TDH_FT_abs_0.npy"],
            [[30.1370, 8.7729],"abs","std_data/TDH/TDH_FT_abs.npy"])
    @unpack
    def test_FT_spectra(self, D_value, spectratype, std_path):
        
        if spectratype == "emi":
            E_offset = 2.28614053/constant.au2ev
        elif spectratype == "abs":
            E_offset = -2.28614053/constant.au2ev
        else:
            assert False

        mol_list = custom_mol_list(None, ph_phys_dim, dis=[Quantity(d) for d in D_value], hartree=True)

        T = Quantity(298)
        insteps = 50
        spectra = tdh.LinearSpectra(spectratype, mol_list, E_offset=E_offset, temperature=T, insteps=insteps)
        nsteps = 300 - 1
        dt = 30.0
        spectra.evolve(dt, nsteps)
        with open(std_path, 'rb') as f:
            std = np.load(f)
        self.assertTrue(np.allclose(spectra.autocorr,std))
        

if __name__ == "__main__":
    print("Test FT_TDH")
    unittest.main()
