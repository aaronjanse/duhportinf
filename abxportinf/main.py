import os
import numpy as np
import json5
from .busdef import BusDef
from ._optimize import map_ports_to_bus, get_mapping_fcost
from ._grouper import get_port_grouper
from . import busdef

# FIXME remove
import time

def get_ports_from_json5(comp_json5_path):
    with open(comp_json5_path) as fin:
        block = json5.load(fin)
    ports = []
    for name, pw in block['definitions']['ports'].items():
        try:
            w, d = np.abs(pw), np.sign(pw)
        except Exception as e:
            print('Warning', (name, pw), 'not correctly parsed')
            w, d = None, np.sign(-1) if pw[0] == '-' else np.sign(1)
        ports.append( (name, w, d) )
    return ports

def get_bus_defs(spec_path):
    assert os.path.isfile(spec_path)
    assert BusDef.is_spec_bus_def(spec_path), \
        "{} does not describe a proper bus abstractionDefinition in JSON5".format(spec_path)
    
    return BusDef.bus_defs_from_spec(spec_path)

def get_bus_matches(ports, bus_defs):
    # perform hierarchical clustering over ports to get tree grouping
    pg, Z, wire_names = get_port_grouper(ports)
    
    # pass over all port groups and compute fcost to prioritize potential
    # bus pairings to optimize
    # NOTE need to keep track of node id in port group tree to pass back
    # costs and figure out optimal port groupings to expose
    pg_bus_pairings = []
    nid_cost_map = {}

    for nid, port_group in pg.get_initial_port_groups():
        # for each port group, only pair the 5 bus defs with the lowest fcost
        pg_bus_defs = list(sorted(
            [(get_mapping_fcost(port_group, bus_def), bus_def) for bus_def in bus_defs],
            key=lambda x:x[0].value,
        ))[:5]
        l_fcost = pg_bus_defs[0][0]
        pg_bus_pairings.append((nid, l_fcost, port_group, pg_bus_defs))
        nid_cost_map[nid] = l_fcost
    
    # prune port groups in which the lowest fcost is too high to warrant
    # more expensive bus matching
    # NOTE don't bother trying to match a particular port group if all the
    # ports in that group potentially have a better assignment based on
    # fcost
    optimal_nids = pg.get_optimal_groups(nid_cost_map)
    opt_pg_bus_pairings = list(sorted(filter(
        lambda x : (
            # must be on an optimal path for some port
            x[0] in optimal_nids and
            # must have less than 5 direction mismatches in the best case
            # from fcost computation
            x[1].dc < 5 and
            # at least 4 ports in a group
            len(x[2]) > 3
        ),
        pg_bus_pairings,
    ), key=lambda x: x[1]))
    #print('initial pg_bus_pairings', len(pg_bus_pairings))
    #print('opt pg_bus_pairings', len(opt_pg_bus_pairings))

    # perform bus mappings for chosen subset to determine lowest cost bus
    # mapping for each port group
    pg_bus_mappings = []
    nid_cost_map = {}
    stime = time.time()
    for i, (nid, l_fcost, port_group, bus_defs) in enumerate(opt_pg_bus_pairings):
        #print('pairing: {}, lcost:{}, port group size: {}'.format(
        #    i, l_fcost, len(port_group)))
        #print('      ', list(sorted(port_group))[:5])
        bus_mappings = []
        for fcost, bus_def in bus_defs:
            cost, mapping, sideband_ports, match_cost_func = \
                map_ports_to_bus(port_group, bus_def)
            bus_mappings.append((
                cost,
                fcost,
                mapping,
                sideband_ports,
                match_cost_func,
                bus_def,
            ))
        bus_mappings.sort(key=lambda x: x[0])
        lcost = bus_mappings[0][0]
        nid_cost_map[nid] = lcost
    
        pg_bus_mappings.append((
            nid,
            lcost,
            port_group,
            bus_mappings,
        ))

    # choose optimal port groups to expose to the user
    optimal_nids = pg.get_optimal_groups(nid_cost_map)
    opt_pg_bus_mappings = list(sorted(filter(
        lambda x : x[0] in optimal_nids,
        pg_bus_mappings,
    ), key=lambda x: x[1]))

    # return pairings of <port_group, bus_mapping>
    return list(map(lambda x: x[2:], opt_pg_bus_mappings))

def debug_bus_mapping(
    port_group,
    bus_mapping,
):
    (
        cost,
        fcost,
        mapping,
        sideband_ports,
        match_cost_func,
        bus_def,
    ) = bus_mapping

    debug_str = ''
    debug_str += str(bus_def)+'\n'
    debug_str += ('  - cost:{}, fcost:{}'.format(cost, fcost))+'\n'
    debug_str += ('  - mapped')+'\n'
    # display mapped signals in order of best match, staring with required
    # signals
    for (is_opt, is_sideband, cost), pp, bp in sorted(
        [
            (
                (
                    bp in set(bus_def.opt_ports), 
                    pp in sideband_ports,
                    match_cost_func(pp, bp),
                ), 
                pp, 
                bp,
            ) 
            for pp, bp in mapping.items()
        ],
        key=lambda x: x[0],
    ):
        debug_str += ('    - {} cost:{}, {:15s}:{:15s} {}'.format(
            '*sideband cand*' if is_sideband else '',
            match_cost_func(pp, bp),
            str(pp), str(bp),
            'opt' if is_opt else 'req',
        ))+'\n'
    umap_ports = set(port_group) - set(mapping.keys())
    umap_busports = set(bus_def.req_ports) - set(mapping.values())
    if len(umap_ports) > 0:
        debug_str += ('  - umap phy ports')+'\n'
        for port in sorted(umap_ports):
            debug_str += ('    - {}'.format(port))+'\n'
    if len(umap_busports) > 0:
        debug_str += ('  - umap bus ports')+'\n'
        for port in sorted(umap_busports):
            debug_str += ('    - {}'.format(port))+'\n'

    #print(debug_str)
    return debug_str
    
