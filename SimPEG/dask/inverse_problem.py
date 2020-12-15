from ..inverse_problem import BaseInvProblem
import numpy as np
from dask.distributed import Future, get_client
import gc
from ..regularization import BaseComboRegularization, Sparse
from ..data_misfit import BaseDataMisfit
from ..objective_function import BaseObjectiveFunction


def dask_getFields(self, m, store=False, deleteWarmstart=True):
    f = None

    try:
        client = get_client()
        fields = lambda f, x, workers: client.compute(f(x), workers=workers)
    except:
        fields = lambda f, x: f(x)

    for mtest, u_ofmtest in self.warmstart:
        if m is mtest:
            f = u_ofmtest
            if self.debug:
                print("InvProb is Warm Starting!")
            break

    if f is None:
        if isinstance(self.dmisfit, BaseDataMisfit):
            f = fields(self.dmisfit.simulation.fields, m)

        elif isinstance(self.dmisfit, BaseObjectiveFunction):
            f = []
            for objfct in self.dmisfit.objfcts:
                if hasattr(objfct, "simulation"):
                    f += [fields(objfct.simulation.fields, m, objfct.workers)]
                else:
                    f += []

    if isinstance(f, Future) or isinstance(f[0], Future):
        f = client.gather(f)

    if deleteWarmstart:
        self.warmstart = []
    if store:
        self.warmstart += [(m, f)]

    return f


BaseInvProblem.getFields = dask_getFields


def get_dpred(self, m, f=None):
    dpred = []
    if isinstance(self.dmisfit, BaseDataMisfit):
        return self.dmisfit.simulation.dpred(m, f=f)
    elif isinstance(self.dmisfit, BaseObjectiveFunction):
        try:
            client = get_client()
            pred = lambda pred, x, fields: client.compute(pred(x), fields=fields)
        except:
            pred = lambda pred, x, fields: pred(x, f=fields)

        for i, objfct in enumerate(self.dmisfit.objfcts):
            if hasattr(objfct, "simulation"):
                dpred += [pred(objfct.simulation.dpred, m, fields=f[i])]
            else:
                dpred += []
    return dpred


BaseInvProblem.get_dpred = get_dpred


def get_deriv(self, m, f=None):
    _deriv = []
    if isinstance(self.dmisfit, BaseDataMisfit):
        return self.dmisfit.simulation.dpred(m, f=f)
    elif isinstance(self.dmisfit, BaseObjectiveFunction):
        try:
            client = get_client()
            d_x = lambda d_x, x, fields: client.compute(d_x(x), fields=fields)
        except:
            d_x = lambda d_x, x, fields: d_x(x, f=fields)

        for i, objfct in enumerate(self.dmisfit.objfcts):
            if hasattr(objfct, "simulation"):
                _deriv += [d_x(objfct.deriv, m, fields=f[i])]
            else:
                _deriv += []
    return _deriv


BaseInvProblem.get_deriv = get_deriv


def get_deriv2(self, m, v, f=None):
    _deriv2 = []
    if isinstance(self.dmisfit, BaseDataMisfit):
        return self.dmisfit.deriv2(m, v, f=f)
    elif isinstance(self.dmisfit, BaseObjectiveFunction):
        try:
            client = get_client()
            d_x2 = lambda d2_x, x, r, fields: client.compute(d2_x(x, r), fields=fields)
        except:
            d_x2 = lambda d2_x, x, r, fields: d2_x(x, r, f=fields)

        for i, objfct in enumerate(self.dmisfit.objfcts):
            if hasattr(objfct, "simulation"):
                _deriv2 += [d_x2(objfct.deriv2, m, v, fields=f[i])]
            else:
                _deriv2 += []
    return _deriv2


BaseInvProblem.get_deriv2 = get_deriv2


def dask_evalFunction(self, m, return_g=True, return_H=True):
    """evalFunction(m, return_g=True, return_H=True)
    """

    self.model = m
    gc.collect()

    # Store fields if doing a line-search
    f = self.getFields(m, store=(return_g is False and return_H is False))

    # if isinstance(self.dmisfit, BaseDataMisfit):
    phi_d = np.asarray(self.dmisfit(m, f=f))
    self.dpred = self.get_dpred(m, f=f)

    self.reg2Deriv = self.reg.deriv2(m)
    # reg = np.linalg.norm(self.reg2Deriv * self.reg._delta_m(m))
    phi_m = self.reg(m)

    self.phi_d, self.phi_d_last = phi_d, self.phi_d
    self.phi_m, self.phi_m_last = phi_m, self.phi_m

    phi = phi_d + self.beta * phi_m

    # Only works for Tikhonov
    if self.opt.print_type == "ubc":

        self.phi_s = 0.0
        self.phi_x = 0.0
        self.phi_y = 0.0
        self.phi_z = 0.0

        if not isinstance(self.reg, BaseComboRegularization):
            regs = self.reg.objfcts
            mults = self.reg.multipliers
        else:
            regs = [self.reg]
            mults = [1.0]
        for reg, mult in zip(regs, mults):
            if isinstance(reg, Sparse):
                i_s, i_x, i_y, i_z = 0, 1, 2, 3
            else:
                i_s, i_x, i_y, i_z = 0, 1, 3, 5
            dim = reg.regmesh.dim
            self.phi_s += mult * reg.objfcts[i_s](m) * reg.alpha_s
            self.phi_x += mult * reg.objfcts[i_x](m) * reg.alpha_x
            if dim > 1:
                self.phi_z += mult * reg.objfcts[i_y](m) * reg.alpha_y
            if dim > 2:
                self.phi_y = self.phi_z
                self.phi_z += mult * reg.objfcts[i_z](m) * reg.alpha_z

    # phi = phi_d + self.beta * phi_m

    out = (phi,)
    if return_g:
        # phi_dDeriv = self.dmisfit.deriv(m, f=f)
        phi_dDeriv = self.get_deriv(m, f=f)
        phi_mDeriv = self.reg2Deriv * self.reg._delta_m(m)

        if isinstance(phi_dDeriv, list):
            phi_dDeriv = np.sum(np.vstack(phi_dDeriv), axis=0)

        g = np.asarray(phi_dDeriv) + self.beta * phi_mDeriv
        out += (g,)

    if return_H:

        def H_fun(v):
            # phi_d2Deriv = self.dmisfit.deriv2(m, v, f=f)
            phi_d2Deriv = self.get_deriv2(m, v, f=f)
            phi_m2Deriv = self.reg2Deriv * v

            if isinstance(phi_d2Deriv, list):
                phi_d2Deriv = np.sum(np.vstack(phi_d2Deriv), axis=0)
                print('[info] ', phi_d2Deriv.shape)

            H = phi_d2Deriv + self.beta * phi_m2Deriv

            return H

        H = H_fun
        out += (H,)
    return out if len(out) > 1 else out[0]


BaseInvProblem.evalFunction = dask_evalFunction
