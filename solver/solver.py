from argparse import ArgumentParser, Namespace
from gurobipy import Model, GRB, tupledict
from os.path import basename, splitext
from itertools import chain
from typing import Tuple, Optional
import matplotlib.pyplot as plt
import seaborn as sns
import json
import numpy as np


CAPACITY = 5
COST_PER_COURIER_AND_PERIOD = 1.0
COST_PER_PARCEL_AND_PERIOD = COST_PER_COURIER_AND_PERIOD / CAPACITY
EPS = 1e-6


class Instance:
    args: Optional[Namespace]
    i: dict

    reg_multiplier: float
    glb_multiplier: float
    outsourcing_cost_multiplier: float
    outsourcing_cost: float
    dregions: dict
    n_regions: int
    n_areas: int
    n_periods: int
    n_scenarios: int
    periods: list
    scenarios: list
    regions: list
    areas: list
    reg_areas: dict
    area_regs: dict
    sdemand: dict
    srequired: dict
    ub_reg: dict
    ub_global: int
    shifts: Optional[list]
    max_n_shifts: Optional[int]
    instance_file: str
    model: str
    name: str
    ibasename: str

    def __init__(self, args: Optional[Namespace], **kwargs):
        self.args = args

        if self.args is None:
            self.instance_file = kwargs['instance']
        else:
            self.instance_file = self.args.instance

        self.i = self.__load_instance(self.instance_file)
        self.__compute_data(**kwargs)

    def __compute_data(self, **kwargs) -> None:
        if self.args is None:
            self.reg_multiplier = kwargs['regional_multiplier']
            self.glb_multiplier = kwargs['global_multiplier']
            self.outsourcing_cost_multiplier = kwargs['outsourcing_cost_multiplier']
            self.model = kwargs['model']
        else:
            self.reg_multiplier = self.args.regional_multiplier
            self.glb_multiplier = self.args.global_multiplier
            self.outsourcing_cost_multiplier = self.args.outsourcing_cost_multiplier
            self.model = self.args.model

        self.outsourcing_cost = COST_PER_COURIER_AND_PERIOD * self.outsourcing_cost_multiplier
        self.dregions = self.i['geography']['city']['regions']
        self.n_regions = len(self.dregions)
        self.n_areas = sum(len(region['areas']) for region in self.dregions)
        self.n_periods = self.i['num_time_intervals']
        self.n_scenarios = self.i['num_scenarios']
        self.periods = list(range(self.n_periods))
        self.scenarios = list(range(self.n_scenarios))
        
        self.regions = [region['id'] for region in self.dregions]
        self.areas = [area['id'] for region in self.dregions for area in region['areas']]
        self.reg_areas = {
            region['id']: [area['id'] for area in region['areas']] for region in self.dregions
        }
        self.area_regs = {
            area: [region for region in self.regions if area in self.reg_areas[region]][0] for area in self.areas
        }

        self.sdemand = dict()
        self.srequired = dict()

        for scenario in self.i['scenarios']:
            s = scenario['scenario_num']
            for data in scenario['data']:
                a = data['area_id']

                for theta, d in enumerate(data['demand']):
                    self.sdemand[s, a, theta] = d

                for theta, m in enumerate(data['required_couriers']):
                    self.srequired[s, a, theta] = m

        self.ub_reg, self.ub_global = self.__get_ubs()
        self.shifts = self.__get_shifts()

        if self.model == 'partflex':
            if self.args is None:
                self.max_n_shift = kwargs['max_n_shifts']
            else:
                self.max_n_shifts = self.args.max_n_shifts

        self.ibasename = splitext(basename(self.instance_file))[0]
        self.name = self.ibasename + f"_oc={self.outsourcing_cost_multiplier}_rm={self.reg_multiplier}_gm={self.glb_multiplier}"

    def __load_instance(self, instance: str) -> dict:
        with open(instance) as f:
            return json.load(f)
        
    def __get_shifts(self) -> list:
        if self.n_periods == 8:
            shifts = [range(4), range(4, 8)]
        else:
            raise NotImplementedError('Fixed shifts only implemented for n_periods == 8')
        
        assert list(chain(*shifts)) == list(self.periods), \
            f"Expected shifts union to be {list(self.periods)}. It is {list(chain(shifts))}."
        
        return shifts

    def __get_ubs(self) -> Tuple[int]:
        mhat1 = {
            (a, theta): np.mean([
                self.srequired[s, a, theta] for s in self.scenarios
            ]) for a in self.areas for theta in self.periods
        }
        mhat2 = {
            a: np.mean([
                mhat1[a, theta] for theta in self.periods
            ]) for a in self.areas
        }
        ub_reg = {
            region: int(self.reg_multiplier * sum(mhat2[a] for a in self.reg_areas[region])) \
            for region in self.regions
        }
        ub_global = int(self.glb_multiplier * sum(ub_reg.values()))

        return ub_reg, ub_global


class Solver:
    args: Namespace
    i: Instance

    m: Model
    x: tupledict
    omega: tupledict
    y: Optional[tupledict]
    z: Optional[tupledict]
    zplus: Optional[tupledict]
    zminus: Optional[tupledict]

    def __init__(self, args: Namespace, i: Instance):
        self.args = args
        self.i = i

    def __build_base_model(self) -> None:
        self.m = Model()
        self.x = self.m.addVars(self.i.areas, self.i.periods, vtype=GRB.INTEGER, lb=0, obj=1, name='x')
        self.omega = self.m.addVars(self.i.areas, self.i.periods, self.i.scenarios, vtype=GRB.CONTINUOUS, obj=1/self.i.n_scenarios, name='omega')

        self.m.addConstrs((
            sum(self.x[a, theta] for a in self.i.reg_areas[region]) <= self.i.ub_reg[region]
                for region in self.i.regions
                for theta in self.i.periods
        ), name='reg_bound')

        self.m.addConstrs((
            self.x.sum('*', theta) <= self.i.ub_global
            for theta in self.i.periods
        ), name='global_bound')

        self.m.addConstrs((
            self.i.srequired[s, a, theta] * self.omega[a, theta, s] >= \
            (self.i.srequired[s, a, theta] - self.x[a, theta]) * self.i.sdemand[s, a, theta] * self.i.outsourcing_cost
            for a in self.i.areas
            for theta in self.i.periods
            for s in self.i.scenarios
        ), name='set_omega')

    def __add_y_vars(self):
        y_idx = [
            (a1, a2, theta)
            for a1 in self.i.areas
            for a2 in self.i.areas
            for theta in self.i.periods
            if a1 != a2 and self.i.area_regs[a1] == self.i.area_regs[a2]
        ]
        self.y = self.m.addVars(y_idx, vtype=GRB.INTEGER, lb=0, obj=EPS, name='y')

    def __build_fixed_model(self) -> None:
        self.__build_base_model()
        self.__add_y_vars()
        
        self.m.addConstrs((
            sum(self.x[a, theta] for a in self.i.reg_areas[region]) == sum(self.x[a, shift[0]] for a in self.i.reg_areas[region])
            for region in self.i.regions
            for shift in self.i.shifts
            for theta in shift[1:]
        ), name='fix_region_n_couriers_in_shift')

        self.m.addConstrs((
            self.x[a1, theta] == self.x[a1, theta - 1] + self.y.sum('*', a1, theta) - self.y.sum(a1, '*', theta)
            for region in self.i.regions
            for a1 in self.i.reg_areas[region]
            for shift in self.i.shifts
            for theta in shift[1:]
        ), name='flow_balance')

    def __build_flexible_model(self) -> None:
        self.__build_base_model()
        self.__add_y_vars()

        self.zplus = self.m.addVars(self.i.areas, self.i.periods, vtype=GRB.INTEGER, lb=0, name='zplus')
        self.zminus = self.m.addVars(self.i.areas, self.i.periods, vtype=GRB.INTEGER, lb=0, name='zminus')

        if self.i.n_periods == 8:
            shift_len = 4
        else:
            return NotImplementedError('Shift length only implemented for n_periods == 8')
        
        for theta in range(self.i.n_periods - shift_len + 1, self.i.n_periods):
            for a in self.i.areas:
                self.zminus[a, theta].UB = 0

        for theta in range(shift_len - 1):
            for a in self.i.areas:
                self.zplus[a, theta].UB = 0

        self.m.addConstrs((
            sum(self.zminus[a, theta] for a in self.i.reg_areas[region]) == \
            sum(self.zplus[a, theta + shift_len - 1] for a in self.i.reg_areas[region])
            for region in self.i.regions
            for theta in self.i.periods
            if theta < self.i.n_periods + 1 - shift_len
        ), name='fix_region_n_couriers_in_shift')

        self.m.addConstrs((
            self.x[a1, theta] == \
            self.x[a1, theta - 1] + \
            self.y.sum('*', a1, theta) - \
            self.y.sum(a1, '*', theta) + \
            self.zminus[a1, theta] - self.zplus[a1, theta - 1]
            for region in self.i.regions
            for a1 in self.i.reg_areas[region]
            for theta in self.i.periods
            if theta > 0
        ), name='flow_balance')

        self.m.addConstrs((
            self.x[a, 0] == self.zminus[a, 0]
            for a in self.i.areas
        ), name='flow_balance_first_period')

    def __build_partflex_model(self) -> None:
        self.__build_flexible_model()

        if self.i.n_periods == 8:
            shift_len = 4
        else:
            return NotImplementedError('Shift length only implemented for n_periods == 8')

        w_idx = range(self.i.n_periods - shift_len + 1)
        self.w = self.m.addVars(w_idx, vtype=GRB.BINARY, name='w')

        self.m.addConstrs((
            sum(self.zminus[a, theta] for a in self.i.reg_areas[region]) <= self.i.ub_reg[region] * self.w[theta]
            for region in self.i.regions
            for theta in w_idx
        ), name='link_z_w')

        self.m.addConstr(self.w.sum() <= self.i.max_n_shifts, name='limit_n_shifts')

    def plot_couriers(self) -> None:
        mtx = [
            [
                sum(int(self.x[a, theta].X)
                for a in self.i.reg_areas[region])
                for theta in self.i.periods
            ]
            for region in self.regions
        ]

        fig, ax = plt.subplots(figsize=(self.i.n_periods, self.i.n_regions))
        sns.heatmap(mtx, annot=True, linecolor='white', linewidths=1, square=True, cmap='Blues', ax=ax)
        ax.set_xlabel('Period')
        ax.set_ylabel('Region')
        ax.set_yticklabels(self.i.regions)

        fig.tight_layout()
        fig.savefig('couriers.png', dpi=96, bbox_inches='tight')

    def solve_base(self) -> dict:
        self.__build_base_model()
        self.m.optimize()
        return self.__basic_results()

    def solve_fixed(self) -> dict:
        self.__build_fixed_model()
        self.m.optimize()
        return self.__fixed_results()

    def solve_partflex(self) -> dict:
        self.__build_partflex_model()
        self.m.optimize()
        return self.__flex_results()

    def solve_flex(self) -> dict:
        self.__build_flexible_model()
        self.m.optimize()
        return self.__flex_results()

    def __basic_results(self) -> dict:
        results = {
            'instance': self.i.ibasename,
            'model': self.args.model,
            'city': self.i.ibasename.split('_')[0],
            'DB': self.i.i['demand_baseline'],
            'DT': self.i.i['demand_type'],
            'OC': self.args.outsourcing_cost_multiplier,
            'RM': self.i.reg_multiplier,
            'GM': self.i.glb_multiplier,
            'num_periods': self.i.i['num_time_intervals'],
            'num_scenarios': self.i.i['num_scenarios'],
            'obj_value': self.m.ObjVal,
            'elapsed_time': self.m.Runtime,
            'n_variables': self.m.NumVars,
            'n_constraints': self.m.NumConstrs,
            'n_nonzeroes': self.m.NumNZs
        }

        hiring_costs = sum(self.x[a, theta].X for a, theta in self.x)
        outsourcing_costs = self.m.ObjVal - hiring_costs

        hired_couriers = {
            a: [int(self.x[a, theta].X) for theta in self.i.periods] for a in self.i.areas
        }

        outsourced_parcels = dict()
        outsourced_parcels_pct = dict()
        inhouse_parcels = dict()

        for a in self.i.areas:
            outsourced = list()
            outsourced_pct = list()
            inhouse = list()

            for theta in self.i.periods:
                scenarios_with_demand = [s for s in self.i.scenarios if self.i.srequired[s, a, theta] > 0]

                if len(scenarios_with_demand) == 0:
                    outsourced.append(0)
                    outsourced_pct.append(0)
                    inhouse.append(0)
                    continue

                tot_outsourced = sum(
                    (self.i.srequired[s, a, theta] - self.x[a, theta].X) * self.i.sdemand[s, a, theta] / self.i.srequired[s, a, theta] \
                    for s in scenarios_with_demand
                )
                tot_outsourced_pct = sum(
                    100 * (self.i.srequired[s, a, theta] - self.x[a, theta].X) / self.i.srequired[s, a, theta] \
                    for s in scenarios_with_demand
                )
                tot_inhouse = sum(
                    self.i.sdemand[s, a, theta] * self.x[a, theta].X / self.i.srequired[s, a, theta] \
                    for s in scenarios_with_demand
                )

                # If we hire more couriers than we need, we don't outsource a negative amount,
                # we outsource zero.
                tot_outsourced = max(tot_outsourced, 0)
                tot_outsourced_pct = max(tot_outsourced_pct, 0)

                avg_outsourced = tot_outsourced / len(scenarios_with_demand)
                avg_outsourced_pct = tot_outsourced_pct / len(scenarios_with_demand)
                avg_inhouse = tot_inhouse / len(scenarios_with_demand)

                outsourced.append(avg_outsourced)
                outsourced_pct.append(avg_outsourced_pct)
                inhouse.append(avg_inhouse)

            outsourced_parcels[a] = outsourced
            outsourced_parcels_pct[a] = outsourced_pct
            inhouse_parcels[a] = inhouse

        regional_hired_pct = {
            region: 100 * sum(self.x[a, theta].X for a in self.i.reg_areas[region] for theta in self.i.periods) / (self.i.ub_reg[region] * self.i.n_periods) \
            for region in self.i.regions
        }
        regional_avg_hired_pct = np.mean(list(regional_hired_pct.values()))
        global_avg_hired_pct = 100 * sum(self.x[a, theta].X for a, theta in self.x) / (self.i.ub_global * self.i.n_periods)

        if hasattr(self, 'y') and self.y is not None:
            pct_movement = list()

            for r in self.i.regions:
                for theta in self.i.periods:
                    movements = sum(
                        self.y[a1, a2, theta].X
                        for a1 in self.i.areas
                        for a2 in self.i.areas
                        if a1 != a2 and self.i.area_regs[a1] == r and self.i.area_regs[a2] == r
                    )

                    employed = sum(
                        self.x[a, theta].X
                        for a in self.i.reg_areas[r]
                    )

                    if employed == 0:
                        pct_movement.append(0.0)
                    else:
                        pct_movement.append(100 * movements / employed)

            results['courier_moved_pct'] = np.mean(pct_movement)

        results['hiring_costs'] = hiring_costs
        results['outsourcing_costs'] = outsourcing_costs
        results['hired_couriers'] = hired_couriers
        results['outsourced_parcels'] = outsourced_parcels
        results['inhouse_parcels'] = inhouse_parcels
        results['regional_hired_pct'] = regional_hired_pct
        results['regional_avg_hired_pct'] = regional_avg_hired_pct
        results['global_avg_hired_pct'] = global_avg_hired_pct

        return results

    def __fixed_results(self) -> dict:
        results = self.__basic_results()
        results['n_shift_start_periods'] = 2
        return results

    def __flex_results(self) -> dict:
        results = self.__basic_results()

        periods_with_start = 0
        for theta in self.i.periods:
            if not any((a, theta) in self.zminus for a in self.i.areas):
                continue
            if any(self.zminus[a, theta].X > 0.1 for a in self.i.areas):
                periods_with_start += 1

        results['periods_with_start'] = periods_with_start
        results['periods_with_start_pct'] = 100 * periods_with_start / self.i.n_periods

        return results


if __name__ == '__main__':
    parser = ArgumentParser(prog='solver', description='Solves one of the shift scheduling models for LMD')
    parser.add_argument('-m', '--model', action='store', choices=('base', 'fixed', 'partflex', 'flex'), required=True)
    parser.add_argument('-i', '--instance', action='store', required=True)
    parser.add_argument('-c', '--outsourcing-cost-multiplier', action='store', type=float, required=True)
    parser.add_argument('-r', '--regional-multiplier', action='store', type=float, required=True)
    parser.add_argument('-g', '--global-multiplier', action='store', type=float, required=True)
    parser.add_argument('-u', '--max-n-shifts', action='store', type=int)
    parser.add_argument('-o', '--output', action='store')
    args = parser.parse_args()

    i = Instance(args=args)
    solver = Solver(args=args, i=i)

    def output_file(args):
        if args.output is not None:
            return args.output
        elif args.model == 'base':
            return "../results/results_" + i.name + "_model=base.json"
        elif args.model == 'fixed':
            return "../results/results_" + i.name + "_model=fixed.json"
        elif args.model == 'partflex':
            return "../results/results_" + i.name + "_mu=" + str(args.max_n_shifts) + "_model=partflex.json"
        elif args.model == 'flex':
            return "../results/results_" + i.name + "_model=flex.json"

    if args.model == 'base':
        results = solver.solve_base()
    elif args.model == 'fixed':
        results = solver.solve_fixed()
    elif args.model == 'partflex':
        results = solver.solve_partflex()
    elif args.model == 'flex':
        results = solver.solve_flex()
        
    with open(output_file(args), 'w') as f:
        json.dump(results, f, indent=2)