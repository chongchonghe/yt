"""
AMR kD-Tree Framework

Authors: Samuel Skillman <samskillman@gmail.com>
Affiliation: University of Colorado at Boulder

Homepage: http://yt-project.org/
License:
  Copyright (C) 2010-2011 Samuel Skillman.  All Rights Reserved.

  This file is part of yt.

  yt is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 3 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
from yt.funcs import *
import numpy as na
from amr_kdtools import Node, kd_is_leaf, kd_sum_volume, kd_node_check, \
        depth_traverse, viewpoint_traverse, add_grids
from yt.utilities.parallel_tools.parallel_analysis_interface \
    import ParallelAnalysisInterface
from yt.visualization.volume_rendering.grid_partitioner import HomogenizedVolume
from yt.utilities.lib.grid_traversal import PartitionedGrid
import pdb

def my_break():
    my_debug = False
    if my_debug: pdb.set_trace()

class Tree(object):
    trunk = None
    pf = None
    _id_offset = None
    min_level = None
    max_level = None
    comm_rank = 0
    comm_size = 1
    def __init__(self, pf, comm_rank, comm_size, left=None, right=None, 
            min_level=None, max_level=None):

        self.pf = pf
        self._id_offset = self.pf.h.grids[0]._id_offset
        if left is None:
            left = na.array([-na.inf]*3)
        if right is None:
            right = na.array([na.inf]*3)

        if min_level is None: min_level = 0
        if max_level is None: max_level = pf.h.max_level
        self.min_level = min_level
        self.max_level = max_level
        self.comm_rank = comm_rank
        self.comm_size = comm_size
        self.trunk = Node(None, None, None,
                left, right, None, 1)
        self.build()

    def add_grids(self, grids):
        lvl_range = range(self.min_level, self.max_level)
        if grids is None:
            level_iter = self.pf.hierarchy.get_levels()
            while True:
                try:
                    grids = level_iter.next()
                except:
                    break
                if grids[0].Level not in lvl_range: continue
                gles = na.array([g.LeftEdge for g in grids])
                gres = na.array([g.RightEdge for g in grids])
                gids = na.array([g.id for g in grids])

                add_grids(self.trunk, gles, gres, gids, self.comm_rank, self.comm_size)
                del gles, gres, gids, grids
        else:
            gles = na.array([g.LeftEdge for g in grids])
            gres = na.array([g.RightEdge for g in grids])
            gids = na.array([g.id for g in grids])

            add_grids(self.trunk, gles, gres, gids, self.comm_rank, self.comm_size)
            del gles, gres, gids, grids


    def build(self, grids = None):
        self.add_grids(grids)

    def check_tree(self):
        for node in depth_traverse(self):
            if node.grid is None:
                continue
            grid = self.pf.h.grids[node.grid - self._id_offset]
            dds = grid.dds
            gle = grid.LeftEdge
            gre = grid.RightEdge
            li = na.rint((node.left_edge-gle)/dds).astype('int32')
            ri = na.rint((node.right_edge-gle)/dds).astype('int32')
            dims = (ri - li).astype('int32')
            assert(na.all(grid.LeftEdge <= node.left_edge))
            assert(na.all(grid.RightEdge >= node.right_edge))
            # print grid, dims, li, ri

        # Calculate the Volume
        vol = kd_sum_volume(self.trunk)
        mylog.debug('AMRKDTree volume = %e' % vol)
        kd_node_check(self.trunk)


class AMRKDTree(HomogenizedVolume):
    current_vcds = []
    tree = None
    no_ghost = True
    fields = ['Density']
    log_fields = [True]
    _id_offset = None
    current_saved_grids = []
    pf = None
    bricks = []
    brick_dimensions = [] 
    _initialized = False
    def __init__(self, pf,  l_max=None, le=None, re=None,
                 fields=None, no_ghost=False, min_level=None, max_level=None,
                 tree_type='domain',log_fields=None, merge_trees=False):

        ParallelAnalysisInterface.__init__(self)

        self.pf = pf
        self.l_max = l_max
        if fields is None: fields = ["Density"]
        self.fields = ensure_list(fields)

        self.no_ghost = no_ghost
        if log_fields is not None:
            log_fields = ensure_list(log_fields)
        else:
            log_fields = [self.pf.field_info[field].take_log
                         for field in self.fields]

        self.log_fields = log_fields
        self._id_offset = pf.h.grids[0]._id_offset

        if le is None:
            self.le = pf.domain_left_edge
        else:
            self.le = na.array(le)
        if re is None:
            self.re = pf.domain_right_edge
        else:
            self.re = na.array(re)

        print 'Building tree with le,re ', self.le, self.re
        self.tree = Tree(pf, self.comm.rank, self.comm.size, self.le, self.re, min_level=min_level, max_level=max_level)

    def initialize_source(self):
        if self._initialized : return
        bricks = []
        for b in self.traverse():
            bricks.append(b)
        self.bricks = na.array(bricks)
        self.brick_dimensions = na.array(self.brick_dimensions)
        self._initialized = True

    def traverse(self, viewpoint=None):
        if viewpoint is None:
            for node in depth_traverse(self.tree):
                if kd_is_leaf(node) and node.grid is not None:
                    yield self.get_brick_data(node)
        else:
            for node in viewpoint_traverse(self.tree, viewpoint):
                if kd_is_leaf(node) and node.grid is not None:
                    yield self.get_brick_data(node)

    def get_brick_data(self, node):
        if node.data is not None: return node.data
        grid = self.pf.h.grids[node.grid - self._id_offset]
        dds = grid.dds
        gle = grid.LeftEdge
        gre = grid.RightEdge
        li = na.rint((node.left_edge-gle)/dds).astype('int32')
        ri = na.rint((node.right_edge-gle)/dds).astype('int32')
        dims = (ri - li).astype('int32')
        assert(na.all(grid.LeftEdge <= node.left_edge))
        assert(na.all(grid.RightEdge >= node.right_edge))

        if grid in self.current_saved_grids:
            dds = self.current_vcds[self.current_saved_grids.index(grid)]
        else:
            dds = []
            for i,field in enumerate(self.fields):
                vcd = grid.get_vertex_centered_data(field,smoothed=True,no_ghost=self.no_ghost).astype('float64')
                if self.log_fields[i]: vcd = na.log10(vcd)
                dds.append(vcd)
                self.current_saved_grids.append(grid)
                self.current_vcds.append(dds)

        data = [d[li[0]:ri[0]+1,
                  li[1]:ri[1]+1,
                  li[2]:ri[2]+1].copy() for d in dds]

        brick = PartitionedGrid(grid.id, data,
                                node.left_edge.copy(),
                                node.right_edge.copy(),
                                dims.astype('int64'))
        node.data = brick
        if not self._initialized: self.brick_dimensions.append(dims)
        return brick

if __name__ == "__main__":
    from yt.mods import *
    from time import time
    pf = load('/Users/skillman/simulations/DD1717/DD1717')
    pf.h

    t1 = time()
    hv = AMRKDTree(pf)
    t2 = time()

    print kd_sum_volume(hv.tree.trunk)
    print kd_node_check(hv.tree.trunk)
    print 'Time: %e seconds' % (t2-t1)


