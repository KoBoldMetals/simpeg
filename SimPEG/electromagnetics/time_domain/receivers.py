import numpy as np
import properties
import scipy.sparse as sp

from ...survey import BaseTimeRx
from ...utils import Zero, mkvc


class BaseRx(BaseTimeRx):
    """
    Time domain receiver base class

    :param numpy.ndarray locations: receiver locations (ie. :code:`np.r_[x,y,z]`)
    :param numpy.ndarray times: times
    :param string orientation: receiver orientation 'x', 'y' or 'z' or numpy array
    """

    use_source_receiver_offset = properties.Bool(
        "Use source-receiver offset", default=False
    )

    def __init__(self, locations, times, orientation=None, **kwargs):
        proj = kwargs.pop("projComp", None)
        if proj is not None:
            self.projComp = proj
        else:
            if type(orientation) is str:
                assert orientation.lower() in [
                    "x",
                    "y",
                    "z",
                ], "'orientation' must be 'x', 'y', 'z', or a unit numpy array vector of shape (3,)."
                self.orientation = orientation
            elif type(orientation) is np.ndarray:
                assert orientation.shape == (
                    3,
                ), "'orientation' must be 'x', 'y', 'z', or a unit numpy array vector of shape (3,)."
                self.orientation = orientation / np.linalg.norm(orientation)
            else:
                raise Exception(
                    "'orientation' must be 'x', 'y', 'z', or a unit numpy array vector of shape (3,)."
                )
        super().__init__(locations=locations, times=times, **kwargs)

    def projGLoc(self, f):
        """Grid Location projection (e.g. Ex Fy ...)"""
        if type(self.orientation) is str:
            return f._GLoc(self.projField) + self.orientation.lower()
        else:
            return "total"

    def projTLoc(self, f):
        """Time Location projection (e.g. CC N)"""
        return f._TLoc(self.projField)

    def getSpatialP(self, mesh, f):
        """
        Returns the spatial projection matrix.

        .. note::

            This is not stored in memory, but is created on demand.
        """
        if type(self.orientation) is str:
            return mesh.getInterpolationMat(self.locations, self.projGLoc(f))
        else:
            P = Zero()
            for strength, component in zip(self.orientation, ["x", "y", "z"]):
                if strength != 0.0:
                    P += strength * (
                        mesh.getInterpolationMat(
                            self.locations, f._GLoc(self.projField) + component
                        )
                    )
            return P

    def getTimeP(self, time_mesh, f):
        """
        Returns the time projection matrix.

        .. note::

            This is not stored in memory, but is created on demand.
        """
        return time_mesh.getInterpolationMat(self.times, self.projTLoc(f))

    def getP(self, mesh, time_mesh, f):
        """
        Returns the projection matrices as a
        list for all components collected by
        the receivers.

        .. note::

            Projection matrices are stored as a dictionary (mesh, time_mesh) if storeProjections is True
        """
        if (mesh, time_mesh) in self._Ps:
            return self._Ps[(mesh, time_mesh)]

        Ps = self.getSpatialP(mesh, f)
        Pt = self.getTimeP(time_mesh, f)
        P = sp.kron(Pt, Ps)

        if self.storeProjections:
            self._Ps[(mesh, time_mesh)] = P

        return P

    def getTimeP(self, time_mesh, f):
        """
        Returns the time projection matrix.

        .. note::

            This is not stored in memory, but is created on demand.
        """
        # if self.projField == 'dbdt':
        #     return time_mesh.getInterpolationMat(
        #         self.times, self.projTLoc(f)
        #     )*time_mesh.faceDiv
        # else:
        return time_mesh.getInterpolationMat(self.times, self.projTLoc(f))

    def eval(self, src, mesh, time_mesh, f):
        """
        Project fields to receivers to get data.

        :param SimPEG.electromagnetics.time_domain.sources.BaseWaveform src: TDEM source
        :param discretize.base.BaseMesh mesh: mesh used
        :param Fields f: fields object
        :rtype: numpy.ndarray
        :return: fields projected to recievers
        """

        P = self.getP(mesh, time_mesh, f)
        f_part = mkvc(f[src, self.projField, :])
        return P * f_part

    def evalDeriv(self, src, mesh, time_mesh, f, v, adjoint=False):
        """
        Derivative of projected fields with respect to the inversion model times a vector.

        :param SimPEG.electromagnetics.time_domain.sources.BaseWaveform src: TDEM source
        :param discretize.base.BaseMesh mesh: mesh used
        :param discretize.base.BaseMesh time_mesh: time mesh
        :param Fields f: fields object
        :param numpy.ndarray v: vector to multiply
        :rtype: numpy.ndarray
        :return: fields projected to recievers
        """

        P = self.getP(mesh, time_mesh, f)
        if not adjoint:
            return P * v
        elif adjoint:
            # dP_dF_T = P.T * v #[src, self]
            # newshape = (len(dP_dF_T)/time_mesh.nN, time_mesh.nN )
            return P.T * v  # np.reshape(dP_dF_T, newshape, order='F')


class PointElectricField(BaseRx):
    """
    Electric field TDEM receiver

    :param numpy.ndarray locations: receiver locations (ie. :code:`np.r_[x,y,z]`)
    :param numpy.ndarray times: times
    :param string orientation: receiver orientation 'x', 'y' or 'z'
    """

    def __init__(self, locations=None, times=None, orientation="x", **kwargs):
        self.projField = "e"
        super(PointElectricField, self).__init__(
            locations, times, orientation, **kwargs
        )


class PointMagneticFluxDensity(BaseRx):
    """
    Magnetic flux TDEM receiver

    :param numpy.ndarray locations: receiver locations (ie. :code:`np.r_[x,y,z]`)
    :param numpy.ndarray times: times
    :param string orientation: receiver orientation 'x', 'y' or 'z'
    """

    def __init__(self, locations=None, times=None, orientation="x", **kwargs):
        self.projField = "b"
        super(PointMagneticFluxDensity, self).__init__(
            locations, times, orientation, **kwargs
        )


class PointMagneticFluxTimeDerivative(BaseRx):
    """
    dbdt TDEM receiver

    :param numpy.ndarray locations: receiver locations (ie. :code:`np.r_[x,y,z]`)
    :param numpy.ndarray times: times
    :param string orientation: receiver orientation 'x', 'y' or 'z'
    """

    def __init__(self, locations=None, times=None, orientation="x", **kwargs):
        self.projField = "dbdt"
        super(PointMagneticFluxTimeDerivative, self).__init__(
            locations, times, orientation, **kwargs
        )

    def eval(self, src, mesh, time_mesh, f):

        if self.projField in f.aliasFields:
            return super(PointMagneticFluxTimeDerivative, self).eval(
                src, mesh, time_mesh, f
            )

        P = self.getP(mesh, time_mesh, f)
        f_part = mkvc(f[src, "b", :])
        return P * f_part

    def projGLoc(self, f):
        """Grid Location projection (e.g. Ex Fy ...)"""
        if self.projField in f.aliasFields:
            return super(PointMagneticFluxTimeDerivative, self).projGLoc(f)
        return f._GLoc(self.projField) + self.orientation

    def getTimeP(self, time_mesh, f):
        """
        Returns the time projection matrix.

        .. note::

            This is not stored in memory, but is created on demand.
        """
        if self.projField in f.aliasFields:
            return super(PointMagneticFluxTimeDerivative, self).getTimeP(time_mesh, f)

        return time_mesh.getInterpolationMat(self.times, "CC") * time_mesh.faceDiv


class PointMagneticField(BaseRx):
    """
    Magnetic field TDEM receiver

    :param numpy.ndarray locations: receiver locations (ie. :code:`np.r_[x,y,z]`)
    :param numpy.ndarray times: times
    :param string orientation: receiver orientation 'x', 'y' or 'z'
    """

    def __init__(self, locations=None, times=None, orientation="x", **kwargs):
        self.projField = "h"
        super(PointMagneticField, self).__init__(
            locations, times, orientation, **kwargs
        )


class PointCurrentDensity(BaseRx):
    """
    Current density TDEM receiver

    :param numpy.ndarray locations: receiver locations (ie. :code:`np.r_[x,y,z]`)
    :param numpy.ndarray times: times
    :param string orientation: receiver orientation 'x', 'y' or 'z'
    """

    def __init__(self, locations=None, times=None, orientation="x", **kwargs):
        self.projField = "j"
        super(PointCurrentDensity, self).__init__(
            locations, times, orientation, **kwargs
        )


class PointMagneticFieldTimeDerivative(BaseRx):
    def __init__(self, locations=None, times=None, orientation="x", **kwargs):
        self.projField = "dhdt"
        super(PointMagneticFieldTimeDerivative, self).__init__(
            locations, times, orientation, **kwargs
        )
