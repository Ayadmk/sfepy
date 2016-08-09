"""
Functions for a mesh refinement with hanging nodes.
"""
import numpy as nm

from sfepy.base.base import assert_
from sfepy.discrete import Integral, Functions, Function
from sfepy.discrete.fem import Mesh, FEDomain, Field

# Rows = facets of reference cell, columns = [sub_cell_i, local facet_i]
refine_edges_2_4 = nm.array([[[0, 0], [1, 3]],
                             [[1, 0], [2, 3]],
                             [[2, 0], [3, 3]],
                             [[3, 0], [0, 3]]])

refine_faces_3_8 = nm.array([[[0, 0], [1, 0], [2, 0], [3, 0]],
                             [[0, 1], [3, 2], [4, 2], [7, 1]],
                             [[0, 2], [1, 1], [4, 1], [5, 2]],
                             [[4, 0], [5, 0], [6, 0], [7, 0]],
                             [[1, 2], [2, 1], [5, 1], [6, 2]],
                             [[2, 2], [3, 1], [6, 1], [7, 2]]])

refine_edges_3_8 = nm.array([[[0, 0], [1, 3]],
                             [[1, 0], [2, 3]],
                             [[2, 0], [3, 3]],
                             [[3, 0], [0, 3]],
                             [[4, 3], [5, 0]],
                             [[5, 3], [6, 0]],
                             [[6, 3], [7, 0]],
                             [[7, 3], [4, 0]]])

def find_level_interface(domain, refine_flag):
    """
    Find facets of the coarse mesh that are on the coarse-refined cell
    boundary.

    ids w.r.t. current mesh:
    - facets: global, local w.r.t. cells[:, 0], local w.r.t. cells[:, 1]

    - interface cells:
      - cells[:, 0] - cells to refine
      - cells[:, 1] - their facet sharing neighbors
      - cells[:, 2] - their facet sharing neighbors w.r.t. the locally refined
        mesh.
    """
    if not refine_flag.any():
        facets = nm.zeros((0, 3), dtype=nm.uint32)
        cells = nm.zeros((0, 2), dtype=nm.uint32)
        return facets, cells, None, None

    def _get_refine(coors, domain=None):
        return nm.nonzero(refine_flag)[0]

    def _get_coarse(coors, domain=None):
        return nm.nonzero(1 - refine_flag)[0]

    get_refine = Function('get_refine', _get_refine)
    get_coarse = Function('get_coarse', _get_coarse)
    functions = Functions([get_refine, get_coarse])
    region0 = domain.create_region('coarse', 'cells by get_coarse',
                                   functions=functions, add_to_regions=False,
                                   allow_empty=True)
    region1 = domain.create_region('refine', 'cells by get_refine',
                                   functions=functions, add_to_regions=False)

    facets = nm.intersect1d(region0.facets, region1.facets)

    cmesh = domain.mesh.cmesh
    dim = cmesh.dim
    cmesh.setup_connectivity(dim - 1, dim)
    cells, offs = cmesh.get_incident(dim, facets, dim - 1,
                                     ret_offsets=True)
    assert_((nm.diff(offs) == 2).all())

    ii = cmesh.get_local_ids(facets, dim - 1, cells, offs, dim)

    ii = ii.reshape((-1, 2))
    cells = cells.reshape((-1, 2))

    ii = nm.where(refine_flag[cells], ii[:, :1], ii[:, 1:])
    cells = nm.where(refine_flag[cells], cells[:, :1], cells[:, 1:])

    facets = nm.c_[facets, ii]

    # Indices of non-refined cells in the level-1 mesh.
    ii = nm.searchsorted(region0.cells, cells[:, 1])
    cells = nm.c_[cells, ii]

    return facets, cells, region0, region1

def refine_region(domain0, region0, region1):
    """
    Coarse cell sub_cells[ii, 0] in mesh0 is split into sub_cells[ii, 1:] in
    mesh1.
    """
    if region1 is None:
        return domain0, None

    mesh0 = domain0.mesh
    mesh1 = Mesh.from_region(region1, mesh0)
    domain1 = FEDomain('d', mesh1)
    domain1r = domain1.refine()
    mesh1r = domain1r.mesh

    sub_cells = nm.empty((region1.shape.n_cell, 5), dtype=nm.uint32)
    sub_cells[:, 0] = region1.cells
    aux = nm.arange(4 * region1.shape.n_cell, dtype=nm.uint32).reshape((-1, 4))
    sub_cells[:, 1:] = region0.shape.n_cell + aux

    coors0, vgs0, conns0, mat_ids0, descs0 = mesh0._get_io_data()

    coors, vgs, _conns, _mat_ids, descs = mesh1r._get_io_data()

    conn0 = mesh0.get_conn(domain0.mesh.descs[0])
    conns = [nm.r_[conn0[region0.cells], _conns[0]]]
    mat_ids = [nm.r_[mat_ids0[0][region0.cells], _mat_ids[0]]]
    mesh = Mesh.from_data('a', coors, vgs, conns, mat_ids, descs)
    domain = FEDomain('d', mesh)

    return domain, sub_cells

def find_facet_substitutions(facets, cells, sub_cells, refine_facets):
    """
    Find facet substitutions in connectivity.

    sub = [coarse cell, coarse facet, fine1 cell, fine1 facet, fine2 cell,
           fine2 facet]
    """
    subs = []
    for ii, fac in enumerate(facets):
        fine = cells[ii, 0]
        coarse = cells[ii, 2]

        isub = nm.searchsorted(sub_cells[:, 0], fine)
        refined = sub_cells[isub, 1:]
        rf = refine_facets[fac[1]]
        used = refined[rf[:, 0]]
        fused = rf[:, 1]

        master = [coarse, fac[2]]
        slave = zip(used, fused)
        sub = nm.r_[[master], slave].ravel()

        # !!!!!
        print ii, fac, fine, coarse, isub, refined, used
        subs.append(sub)

    subs = nm.array(subs)
    return subs

def refine(domain0, refine, gsubs=None):
    facets, cells, region0, region1 = find_level_interface(domain0, refine)

    print nm.c_[facets, cells]

    domain, sub_cells = refine_region(domain0, region0, region1)

    #_plot(domain.mesh.cmesh)

    if facets.shape[0] > 0:
        desc = domain0.mesh.descs[0]
        conn0 = domain0.mesh.get_conn(desc)
        conn1 = domain.mesh.get_conn(desc)

        print conn1

        print conn1[sub_cells[:, 1:]]
        print conn0[sub_cells[:, 0]]

        print cells[:, 2]
        print conn0[cells[:, 1]]
        print conn1[cells[:, 2]]
        assert_((conn0[cells[:, 1]] == conn1[cells[:, 2]]).all())

    desc = domain0.mesh.descs[0]
    if desc == '2_4':
        gsubs1 = find_facet_substitutions(facets, cells, sub_cells,
                                          refine_edges_2_4)

    gsubs1 = find_facet_substitutions(facets, cells, sub_cells)
    if gsubs is None:
        gsubs = gsubs1 if len(gsubs1) else None

    elif len(gsubs1):
        mods = nm.zeros(domain.shape.n_el + 1, dtype=nm.int32)
        mods[refine > 0] = -1
        mods = nm.cumsum(mods)

        print gsubs
        gsubs[:, [0, 2, 4]] += mods[gsubs[:, [0, 2, 4]]]
        print gsubs

        gsubs = nm.r_[gsubs, gsubs1]

    return domain, gsubs

def eval_basis_transform(field, gsubs):
    """
    """
    gel = field.gel
    ao = field.approx_order

    conn = [gel.conn]
    mesh = Mesh.from_data('a', gel.coors, None, [conn], [nm.array([0])],
                          [gel.name])
    cdomain = FEDomain('d', mesh)
    comega = cdomain.create_region('Omega', 'all')
    rcfield = Field.from_args('rc', field.dtype, 1, comega, approx_order=ao)

    fdomain = cdomain.refine()
    fomega = fdomain.create_region('Omega', 'all')
    rffield = Field.from_args('rf', field.dtype, 1, fomega, approx_order=ao)

    subs = [0, 0, 0, 0, 1, 3]

    ef = rffield.efaces
    fcoors = rffield.get_coor()

    c0 = fcoors[rffield.econn[subs[2], ef[subs[3]]]]
    c1 = fcoors[rffield.econn[subs[4], ef[subs[5]]]]

    n_fdof = c0.shape[0]

    coors = nm.r_[c0, c1]

    integral = Integral('i', coors=coors, weights=nm.ones_like(coors[:, 0]))

    bf = rcfield.get_base('v', False, integral)

    print bf
    print bf[:n_fdof, 0, ef[subs[1]]], ef[subs[3]]
    print bf[n_fdof:, 0, ef[subs[1]]], ef[subs[5]]

    transform = nm.tile(nm.eye(field.econn.shape[1]),
                        (field.econn.shape[0], 1, 1))
    if gsubs is None:
        return transform

    for ii, sub in enumerate(gsubs):
        print ii, sub
        mtx = transform[sub[2]]
        print sub[2], mtx
        ix, iy = nm.meshgrid(ef[sub[3]], ef[sub[3]])
        mtx[ix, iy] = bf[:n_fdof, 0, ef[subs[1]]]
        print mtx

        mtx = transform[sub[4]]
        print sub[4], mtx
        ix, iy = nm.meshgrid(ef[sub[5]], ef[sub[5]])
        mtx[ix, iy] = bf[n_fdof:, 0, ef[subs[1]]]
        print mtx

    assert_((nm.abs(transform.sum(1) - 1.0) < 1e-15).all())

    return transform
