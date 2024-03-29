from dolfin import (dx, Constant, assemble_system, TestFunction, 
                    as_backend_type, assemble, PETScOptions, Function, plot, File, 
                    PETScMatrix, PETScVector, MPI)
from matplotlib.pyplot import subplots
import petsc4py
from slepc4py import SLEPc
from petsc4py import PETSc
import numpy as np
import dolfin


class EigenSolver(object):
    
    def __init__(self, 
                 a_k, 
                 a_m,
                 u,
                 bcs=None, 
                 slepc_options={'eps_max_it':100},
                 option_prefix=None,
                 comm=MPI.comm_world, 
                 slepc_eigensolver = None
                ):
        self.comm = comm
        self.slepc_options = slepc_options
        if option_prefix:
            self.E.setOptionsPrefix(option_prefix)
        self.V = u.function_space()
        if type(bcs) == list:
            self.bcs = bcs
        else:
            self.bcs = [bcs]

        # assemble the matrices as petsc mat
        self.K = as_backend_type(assemble(a_k)).mat()
        self.M = as_backend_type(assemble(a_m)).mat()

        # if bcs extract reduced matrices on dofs with no bcs
        if self.bcs:
            self.index_set_not_bc = self.get_interior_index_set(
                self.bcs, self.V)
            self.K = self.K.createSubMatrix(self.index_set_not_bc, self.index_set_not_bc)
            self.M = self.M.createSubMatrix(self.index_set_not_bc, self.index_set_not_bc)
            self.projector = petsc4py.PETSc.Scatter()
            self.projector.create(
                vec_from=self.K.createVecRight(),
                is_from=None,
                vec_to=u.vector().vec(),
                is_to=self.index_set_not_bc
                )

        # set up the eigensolver
        if slepc_eigensolver:
            self.E = slepc_eigensolver
        else:
            self.E = self.eigensolver_setup() 
        self.E.setOperators(self.K,self.M)       
    
    
    def get_interior_index_set(self, boundary_conditions, function_space):
        """Returns the index set with free dofs"""
        # Find dofs affected by the boundary conditions
        bc_dofs = []    
        for bc in boundary_conditions:
            bc_dofs.extend(bc.get_boundary_values().keys()) 
        ownership_range = function_space.dofmap().ownership_range()
        interior_dofs = [x for x in range(ownership_range[0], ownership_range[1]) if x not in bc_dofs]    
        # Create petsc4py.PETSc.IS object with interior degrees of freedom
        index_set = petsc4py.PETSc.IS()
        index_set.createGeneral(interior_dofs)  
        return index_set


    def eigensolver_setup(self):
        E = SLEPc.EPS()
        E.create()        
        E.setType(SLEPc.EPS.Type.KRYLOVSCHUR)
        E.setProblemType(SLEPc.EPS.ProblemType.GHEP)
        #E.setWhichEigenpairs(E.Which.SMALLEST_MAGNITUDE)
        E.setWhichEigenpairs(E.Which.TARGET_MAGNITUDE)
        E.setTarget(0) 
        st = E.getST()
        st.setType('sinvert')
        return E
    
    def solve(self, n_eig):
        E = self.E
        E.setDimensions(n_eig)
        self.set_options(self.slepc_options)
        E.setFromOptions()
        E.solve()
        # print info
        its = E.getIterationNumber()
        eps_type = E.getType()
        self.nev, ncv, mpd = E.getDimensions()
        tol, maxit = E.getTolerances()
        self.nconv = E.getConverged()
        print("Solution method: {:s}, stopping condition: tol={:.4g}, maxit={:d}".format(eps_type,tol, maxit))
        print("Number of converged/requested eigenvalues with {:d} iterations  : {:d}/{:d}".format(its,self.nconv,self.nev))
        return self.nconv, its
        
    def set_options(self,slepc_options):
        print("---- setting additional slepc options -----")
        for (opt, value) in slepc_options.items():
            print("    ",opt,":",value)
            PETScOptions.set(opt,value) 
        print("-------------------------------------------")
        self.E.setFromOptions()
        
    def get_eigenpair(self,i):
        u_r = Function(self.V)
        u_im = Function(self.V)
        v_r, v_i = self.K.createVecs()
        eig = self.E.getEigenpair(i, v_r, v_i)
        if self.bcs:
            self.projector.scatter(vec_from=v_r, vec_to=u_r.vector().vec())
            self.projector.scatter(vec_from=v_i, vec_to=u_im.vector().vec())
        return eig, u_r, u_im
        
    def get_eigenvalues(self,n):
        eigenvalues = [] 
        for i in range(n):
            eig, u_r, u_im = self.get_eigenpair(i)
            eigenvalues.append(eig.real)
        return np.array(eigenvalues)
    
    def get_eigenpairs(self,n):
        eigenvalues = [] 
        eigenvectors_real = [] 
        eigenvectors_im = [] 
        for i in range(n):
            eig, u_r, u_im= self.get_eigenpair(i)
            eigenvalues.append(eig)
            eigenvectors_real.append(u_r)
            eigenvectors_im.append(u_im)
        return np.array(eigenvalues), [eigenvectors_real, eigenvectors_im]
    
    def save_eigenvectors(self,n,file_name="output/modes.pvd",save_imaginary=False):
        eigenvalues = [] 
        eigenvectors = [] 
        file = File(self.comm,file_name)
        for i in range(n):
            eig, u_r, u_im = self.get_eigenpair(i)
            u_r.rename("mode real","mode real")
            file.write(u_r,i)
            if save_imaginary:
                u_im.rename("mode imaginary","mode imaginary")
                file.write(u_im,i)            
        return file_name
    
    def plot_eigenpair(self,i):
        eig, u_r, u_im = self.get_eigenpair(i)
        p = plot(u_r,title="{:d} -- {:2.2f}".format(i,eig))
        return p 
    

if __name__ == "__main__":

    import dolfin
    import ufl
    import numpy as np
    import matplotlib.pyplot as plt
    dolfin.parameters["use_petsc_signal_handler"] = True
    dolfin.parameters["form_compiler"]["cpp_optimize"] = True
    dolfin.parameters["form_compiler"]["representation"] = "uflacs"

    n = 100
    mesh = dolfin.UnitSquareMesh(n,n)
    V = dolfin.FunctionSpace(mesh,'CG',1)
    u = dolfin.Function(V)
    ut = dolfin.TestFunction(V)
    v = dolfin.TrialFunction(V)
    dx = dolfin.Measure("dx",domain=mesh)
    bc = dolfin.DirichletBC(V,0,"on_boundary") 
    a_k = ufl.dot(ufl.grad(ut),ufl.grad(v))*dx
    a_m = ut*v*dx
    eig_solver = EigenSolver(a_k, a_m, u, [bc])
    plt.savefig("operators.png")
    ncv, it = eig_solver.solve(10)
    eigs = eig_solver.get_eigenvalues(ncv)
    plt.figure()
    plt.plot(eigs,'o')
    plt.title("Eigenvalues")
    plt.savefig("eigenvalues.png")

    eig_solver.save_eigenvectors(ncv)
    for i in range(ncv): 
        plt.figure()
        eig_solver.plot_eigenpair(i)
        plt.savefig("output/mode-{:d}.png".format(i))



