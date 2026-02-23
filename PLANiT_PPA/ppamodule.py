import pandas as pd
import numpy as np
import PLANiT_PPA.costutils as _cost
import PLANiT_PPA.KEPCOutils as _kepco
import pypsa 

class PPAModel:
    """
    This class encapsulates the entire PPA modeling code.
    The only parts you need to change are passed as arguments.
    to the constructor (above the ### From here to the end ### comment).
    """

    def __init__(
            self,
            # --- USER INPUTS (formerly the section above ### From here to the end ###) ---
            loads_config, 
            battery_include, 
            currency_exchange, 
            max_grid_share, 
            sense, 
            rate_increase, 
            selected_sheet, 
            carbonprice_init, 
            carbonprice_rate,
            rec_grid_init,
            rec_reduction,
            rec_include,
            rec_increase,
            smp_limit,
            smp_init,
            smp_increase,
            initial_year,
            end_year,
            model_year,
            # Parameters
            default_params,
            # Battery Parameters
            battery_parameters,
            ppa_payment_type
    ):
        # Store user-defined parameters
        self.loads_config = loads_config
        self.battery_include = battery_include
        self.currency_exchange = currency_exchange
        self.max_grid_share = max_grid_share
        self.sense = sense
        self.rate_increase = rate_increase
        self.selected_sheet = selected_sheet
        self.carbonprice_init = carbonprice_init
        self.carbonprice_rate = carbonprice_rate
        self.rec_grid_init = rec_grid_init
        self.rec_reduction = rec_reduction
        self.smp_limit = smp_limit
        self.smp_init = smp_init
        self.rec_include = rec_include
        self.initial_year = initial_year
        self.end_year = end_year
        self.model_year = model_year
        self.rec_increase = rec_increase
        self.smp_increase = smp_increase
        self.discount_rate = default_params['discount_rate']
        # Internal modeling parameters
        self.default_params = default_params
        self.battery_parameters = battery_parameters
        self.ppa_payment_type = ppa_payment_type

    def run_model(self):
        """
        Execute the entire PPA model.
        Everything after the ### From here to the end ### comment goes here.
        """

        print("\n\n\n ========== PPA Calulcation Starts ========== \n\n\n")

        print("Calculate PV CAPEX")

        # Read grid data
        gridinf_df = pd.read_csv("database/grid.csv", index_col=0)

        solar_capex_df = gridinf_df['solar_capex']
        wind_capex_df = gridinf_df['wind_capex']
        irr = self.default_params['irr']

        # Create rec_grid
        if self.rec_reduction:
            rec_grid = pd.DataFrame(
                index=range(self.initial_year, self.end_year + 1),
                data={
                    'value': np.linspace(self.rec_grid_init, 0.0,
                                         len(range(self.initial_year, self.end_year + 1)))
                }
            ).loc[self.model_year:self.end_year]
        else:
            rec_grid = _kepco.create_rec_grid(
                self.initial_year,
                self.end_year,
                initial_rec=self.rec_grid_init,
                rate_increase=self.rec_increase,
            ).loc[self.model_year:self.end_year]

        rec_ren = rec_grid.loc[self.model_year].value if self.rec_include == True else 0

        smp_grid = _kepco.create_rec_grid(
            self.initial_year,
            self.end_year,
            initial_rec=self.smp_init,
            rate_increase=self.smp_increase
        ).loc[self.model_year]['value'] if self.smp_limit == True else 0

        # Carbon price
        if self.carbonprice_init == 'NGFS':
            carbonprice_grid = _cost.process_and_interpolate_annual_data(
                'database/NGFS_carbonprice.xlsx',
                'Net Zero 2050'
            ).loc[self.model_year:self.end_year]
            carbonprice_grid *= self.currency_exchange  # KRW/kgCO2
        else:
            carbonprice_grid = _kepco.create_rec_grid(
                self.initial_year,
                self.end_year,
                initial_rec=self.carbonprice_init,
                rate_increase=self.carbonprice_rate
            ).loc[self.model_year:self.end_year]

        # ETS requirement
        ets_requirement = pd.DataFrame(
            index=range(self.initial_year, self.end_year + 1),
            data={
                'value': np.linspace(0.9, 0.0, len(range(self.initial_year, self.end_year + 1)))
            }
        ).loc[self.model_year:self.end_year]
        ets_requirement = 1 - ets_requirement

        # Helper function for cost analysis
        def calculate_and_analyze(grid, area_column, landcost_factor, availability, total_cost_column, plot):
            cost_df = _cost.calculate_capex_by_grid(
                grid=grid,
                dist_cols=self.default_params['dist_cols'],
                capex=solar_capex_df.loc[self.model_year],
                connection_fees=self.default_params['connection_fees'],
                landcost_factor=landcost_factor,
                power_density=self.default_params['power_density'],
                availability=availability,
                area=area_column,
                others=None,
                digits=self.default_params['digits']
            )
            stats_df = _cost.analyze_cost_data(
                cost_df=cost_df,
                minimum_capacity=self.default_params['min_capacity'],
                total_cost_column=total_cost_column,
                bin=self.default_params['bin_size'],
                plot=plot
            )
            return stats_df

        # Process Grid
        grid = _cost.process_grid_site_data(
            buffer_distance=self.default_params['buffer'],
            site_path="gisdata/clusterpolygon.gpkg",
            grid_path="gisdata/db_semippa.gpkg",
            output_path="gisdata/grids_within_modified_buffer.gpkg"
        )

        # Analyze PV Costs
        pv_stats = calculate_and_analyze(
            grid=grid,
            area_column='area_photo',
            landcost_factor=2,
            availability=0.5,
            total_cost_column='total_cost_distance_0',
            plot=False
        )

        # Analyze Agricultural PV Costs
        agri_stats = calculate_and_analyze(
            grid=grid,
            area_column='area_agrivol',
            landcost_factor=0,
            availability=0.1,
            total_cost_column='total_cost_distance_0',
            plot=False
        )

        print("Calculate LCOE")

        # Capacity factors
        capacity_factors = pd.DataFrame(
            index=range(self.initial_year, self.end_year + 1),
            data={
                'value': np.linspace(0.17, 0.25, (self.end_year - self.initial_year + 1)),
            }
        ).loc[self.model_year:self.end_year]

        # OPEX, lifetime, and discount rate
        opex_rate = 0.025
        lifetime = 20

        pv_stats['LCOE'] = _cost.annualise_capital_cost(
            capacity_factors.loc[self.model_year].astype(float).value,
            pv_stats['max_cost'],
            pv_stats['max_cost'] / pv_stats['bin_capacity_gw'] / 1000 * opex_rate,
            lifetime,
            discount_rate = self.discount_rate,
            rec_cost=rec_ren,
            min_smp=smp_grid,
            plot=False
        )

        # pv_stats['LCOE'] = pv_stats['LCOE'] * (1+irr)

        agri_stats['LCOE'] = _cost.annualise_capital_cost(
            capacity_factors.loc[self.model_year].astype(float).value,
            agri_stats['max_cost'],
            agri_stats['max_cost'] / agri_stats['bin_capacity_gw'] / 1000 * opex_rate,
            lifetime,
            discount_rate = self.discount_rate,
            rec_cost=rec_ren,
            min_smp=smp_grid,
            plot=False
        )

        # agri_stats['LCOE'] = pv_stats['LCOE'] * (1 + irr)

        pv_stats['type'] = 'PV'
        agri_stats['type'] = 'agriPV'

        df = pd.concat([pv_stats, agri_stats])
        df['region'] = None
        df['capital_cost'] = df['max_cost']

        """
        Import Wind Data
        """
        wind_df = pd.read_excel('database/wind_grid.xlsx', index_col=0)
        wind_df.dropna(subset=['admin_boundaries'], inplace=True)
        wind_df = wind_df[wind_df['admin_boundaries'].str.contains('인천광역시|인천 광역시|경기도|충청남도')]
        wind_df = wind_df[wind_df['DT_m'] <= self.default_params['max_distace']]

        windcapex_rate = gridinf_df['wind_capex'] / gridinf_df['wind_capex'].loc[self.model_year]

        wind_df['LCOE'] = wind_df['LCOE'] * windcapex_rate.loc[self.model_year] * 1000
        wind_df['annualised_cost'] = wind_df['LCOE'] + wind_df['rec_weight'] * rec_ren
        # capacity_factors = np.linspace(0.25, 0.35, num=20)
        wind_df['capacity'] = self.default_params['wind_cap']

        kr_to_en = {
            '충청남도': 'Chungcheongnam-do',
            '경기도': 'Gyeonggi-do',
            '인천광역시': 'Gyeonggi-do'
        }
        wind_df['admin_boundaries'] = wind_df['admin_boundaries'].replace(kr_to_en)

        windcost_dt = {
            region: _cost.analyze_cost_data(
                cost_df=wind_df[wind_df['admin_boundaries'] == region],
                minimum_capacity=self.default_params['min_capacity'],
                total_cost_column='annualised_cost',
                bin=self.default_params['bin_size'],
                plot=False,
                annualised_cost=True
            )
            for region in wind_df['admin_boundaries'].unique()
        }

        windcost_df = pd.concat(windcost_dt).reset_index().rename(
            columns={'level_0': 'region'}
        ).drop(columns='level_1')
        windcost_df['type'] = 'offshore_wind'

        windcost_df['LCOE'] = windcost_df['max_cost']
        windcost_df['capital_cost'] = wind_capex_df.loc[self.model_year]

        cost_df = pd.concat([df, windcost_df])

        cost_df['LCOE'] = cost_df['LCOE'] * (1 + irr)

        cost_df.to_excel('test.xlsx', index=False)

        """
        PPA Modelling starts here
        """
        # Initialize a PyPSA network
        network = pypsa.Network()

        # Create hourly snapshots
        snapshots = pd.date_range(
            f"{self.model_year}-01-01",
            f"{self.model_year}-12-31 23:59:59",
            freq="h"
        )
        network.set_snapshots(snapshots)

        # Add a single bus
        network.add("Bus", "one_bus", carrier="one_node")

        # Read and filter load data
        load_df = pd.read_sql_table('load_patterns', 'sqlite:///database/load_patterns.db').set_index('datetime')
        load_df = load_df.loc[
            (load_df.index >= f"{self.model_year}-01-01") &
            (load_df.index <= f"{self.model_year}-12-31 23:59:59")
            ]

        load_sk = load_df['value'] * self.loads_config["SK"]
        load_samsung = load_df['value'] * self.loads_config["Samsung"]
        total_load = (load_sk + load_samsung).reindex(snapshots, fill_value=0)

        # Single load representing total demand
        network.add(
            "Load",
            "total_load",
            bus="one_bus",
            p_set=total_load
        )

        # Represent KEPCO as a generator (unlimited import)
        network.add(
            "Generator",
            "KEPCO",
            carrier="grid_electricity",
            bus="one_bus",
            p_nom_extendable=False,
            p_nom=0 if self.max_grid_share == 0 else sum(self.loads_config.values()),
            marginal_cost=0
        )

        # Process KEPCO data
        filepath = "database/KEPCO.xlsx"
        temporal_df, contract_fee = _kepco.process_kepco_data(filepath, self.model_year, self.selected_sheet)

        # Align usage fees with snapshots
        # Base usage fee for time-varying
        # and create multi-year price projection
        num_years = self.end_year - 2025 + 1
        multiyearusagefees_df, multiyearcontractfees_df = _kepco.multiyear_pricing(
            temporal_df, contract_fee, 2025, num_years, self.rate_increase, annualised_contract=True
        )

        multiyearusagefees_df['rate'] = multiyearusagefees_df['rate'] + multiyearusagefees_df['contract_fee']
        multiyearusagefees_df['rate'] *= 1000  # krw/MWh

        # 1) Ensure the index is a DatetimeIndex
        multiyearusagefees_df.index = pd.to_datetime(multiyearusagefees_df.index)

        # 2) Filter exactly for `model_year`
        singleyearusagefees_df = multiyearusagefees_df[
            multiyearusagefees_df.index.year == self.model_year
            ]

        # 3) Align with the single-year snapshots (2030-01-01 .. 2030-12-31)
        singleyearusagefees_df = singleyearusagefees_df.reindex(network.snapshots).fillna(method="ffill")

        # 4) Assign
        network.generators_t.marginal_cost["KEPCO"] = singleyearusagefees_df['rate']

        # Add carriers for PPA technologies
        network.add("Carrier", "PV", color='green')
        network.add("Carrier", "agriPV", color='light green')
        network.add("Carrier", "offshore_wind", color='blue')
        network.add("Carrier", "one_node", color='black')

        # # Emission intensity

        # Emission intensity for the model_year (tons/MWh)
        co2intensity = gridinf_df.loc[self.model_year, "co2"] * 0.001  # Convert to tons/MWh

        # Create a time series for the snapshots of the model_year
        co2_time_series = pd.Series(
            co2intensity,  # Same value repeated for all snapshots
            index=snapshots  # Snapshots for the model_year
        )

        network.carriers.loc["grid_electricity", "co2_emissions_per_mwh"] = co2_time_series.mean()

        # Renewable share for the model_year
        renshare = gridinf_df.loc[self.model_year, "ren_share"]

        # Create a time series for the snapshots of the model_year
        renshare_series = pd.Series(
            renshare,  # Same value repeated for all snapshots
            index=snapshots  # Snapshots for the model_year
        )

        network.carriers.loc["grid_electricity", "ren_share"] = renshare_series.mean()

        gridinf_df = gridinf_df.loc[self.initial_year: self.end_year]

        # Add PPA model
        solarpattern_df = \
        pd.read_sql_table('solar_patterns', 'sqlite:///database/solar_patterns.db').set_index('datetime')['q99']
        solarpattern_df = solarpattern_df.loc[
            (solarpattern_df.index >= f"{self.model_year}-01-01") &
            (solarpattern_df.index <= f"{self.model_year}-12-31 23:59:59")
            ].reindex(snapshots, fill_value=0)

        windpattern_df = pd.read_sql_table('wind_patterns', 'sqlite:///database/wind_patterns.db').set_index('index')
        windpattern_df = windpattern_df.loc[
            (windpattern_df.index >= f"{self.model_year}-01-01") &
            (windpattern_df.index <= f"{self.model_year}-12-31 23:59:59")
            ].reindex(snapshots, fill_value=0)

        # Add a curtailment bus to track curtailed energy
        # network.add("Bus", "curtailment_bus", carrier="curtailment")

        for i, row in cost_df.iterrows():

            generator_name = f"{row['type']}_{i}"
            # define the pattern for the PPA technology
            if row['type'] in ['PV', 'agriPV']:
                pattern_df = solarpattern_df
            else:
                # pattern_df = pd.Series(0, index=snapshots)
                pattern_df = windpattern_df.get(row['region'], pd.Series(0, index=snapshots))
            if pattern_df.max() > 1:
                raise ValueError("The pattern_df has values greater than 1")

            # redefine if self_max_grid_share is True:
            if self.max_grid_share == 1:
                p_max_pu = 0

            else: # max ppa share is > 0 then define the ppa payment type
                if self.ppa_payment_type == "Current":
                    p_max_pu = pattern_df

                elif self.ppa_payment_type == "Levelised":
                    capacity_factor = pattern_df.mean()
                    p_max_pu = 1

                else:
                    raise ValueError("Invalid ppa_payment_type. Choose 'Current' or 'Levelised'.")

            network.add(
                "Generator",
                name=generator_name,
                bus='one_bus',
                carrier=row['type'],
                p_nom_extendable=False,
                p_nom=row['bin_capacity_gw'] * 1000,
                lifetime=20,
                marginal_cost=row['LCOE'],
                p_max_pu=p_max_pu
            )

            # Redefine if self_max_grid_share is True
            if self.max_grid_share != 1:  # max PPA share is > 0, then define the PPA payment type
                if self.ppa_payment_type == "Levelised":
                    # Calculate capacity factor and maximum annual generation
                    capacity_factor = pattern_df.mean()
                    max_generation = capacity_factor * row['bin_capacity_gw'] * 1000 * 8760  # MWh

                    # Ensure generator exists before adding its properties
                    if generator_name not in network.generators.index:
                        raise RuntimeError(f"Generator '{generator_name}' not found in the network.")

                    # Update the generator component to include the e_sum_max property
                    network.generators.loc[generator_name, "e_sum_max"] = max_generation
                    # print(f"Set e_sum_max for generator '{generator_name}' to {max_generation:.2f} MWh.")


            # Grid share constraints
        if self.max_grid_share is not None and 0 < self.max_grid_share < 1:
            total_demand = load_df['value'].sum() * (
                    self.loads_config.get('SK', 0) + self.loads_config.get('Samsung', 0))

            network.add(
                "GlobalConstraint",
                "grid_energy_limit",
                type="operational_limit",
                carrier_attribute="grid_electricity",
                sense=self.sense,
                constant=total_demand * self.max_grid_share
            )

        network.add("Carrier", "battery", color="red")  # Define battery carrier

            # Battery
        if self.battery_parameters['include']:
            network.add(
                "StorageUnit",
                name="Battery",
                bus="one_bus",
                carrier="battery",
                capital_cost=self.battery_parameters["capital_cost_per_mw"],
                marginal_cost=0,
                max_hours=self.battery_parameters["max_hours"],
                efficiency_store=self.battery_parameters["efficiency_store"],
                efficiency_dispatch=self.battery_parameters["efficiency_dispatch"],
                p_nom_extendable=True,
                cyclic_state_of_charge=True
            )

        result = network.optimize(solver_name = 'glpk')

        import logging

        # Configure logging
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(__name__)

        if result is not None:  # Check if the network is solved
            logger.info("Optimization successfully solved. Proceeding with post-simulation analysis.")

            import logging

            # Configure logging
            logging.basicConfig(level=logging.INFO)
            logger = logging.getLogger(__name__)

            if self.ppa_payment_type == "Levelised":
                # Post-optimization: Verify constraints for each generator
                for i, row in cost_df.iterrows():
                    generator_name = f"{row['type']}_{i}"

                    # Ensure the generator exists in the network
                    if generator_name not in network.generators.index:
                        logger.warning(f"Generator '{generator_name}' not found in the network. Skipping verification.")
                        continue

                    # Retrieve the total energy supplied by the generator
                    total_energy_supplied = network.generators_t.p[generator_name].sum()

                    # Retrieve the e_sum_max constraint value
                    e_sum_max = network.generators.loc[generator_name, "e_sum_max"]

                    # Log the generator's energy-related details
                    logger.info(f"Generator: {generator_name}")
                    logger.info(f"  Total Energy Supplied: {total_energy_supplied:.2f} MWh")
                    logger.info(f"  Maximum Allowed Energy (e_sum_max): {e_sum_max:.2f} MWh")

                    # Check if the e_sum_max constraint is respected
                    if total_energy_supplied > e_sum_max + 1e-3:
                        logger.error(f"  ❌ Energy constraint violated for {generator_name}!")
                    else:
                        logger.info(f"  ✅ Energy constraint respected for {generator_name}.")

                    # Check peak power
                    actual_peak = network.generators_t.p[generator_name].max()
                    p_nom_opt = network.generators.at[generator_name, "p_nom_opt"]

                    # Log the generator's power-related details
                    logger.info(f"  Actual Peak Power Supplied: {actual_peak:.2f} MW")
                    logger.info(f"  Nominal Power Capacity: {p_nom_opt:.2f} MW")

                    if actual_peak > p_nom_opt + 1e-3:
                        logger.error(f"  ❌ Peak power constraint violated for {generator_name}!")
                    else:
                        logger.info(f"  ✅ Peak power constraint respected for {generator_name}.")

                    logger.info("-" * 40)

            # Analyze results
            output_analysis = {}
            analysis_period = self.end_year - self.model_year

            active_generators = network.generators_t.p.loc[:, network.generators_t.p.max() > 0]
            print("Details of generators with positive dispatch:")
            print(active_generators.columns.tolist())

            # Calculate generation by generator
            generation_by_generator = network.generators_t.p.loc[:, active_generators.columns].sum()
            output_analysis["generation by generator (MWh)"] = generation_by_generator

            print("\nGeneration by Generator (MWh):")
            print(generation_by_generator)

            # Group generation
            generation_by_carrier = network.generators_t.p.groupby(network.generators.carrier, axis=1).sum()
            print("\nGeneration by Carrier (GWh):")
            print(generation_by_carrier.sum() / 1000)
            total_generation_by_carrier = generation_by_carrier.sum() / 1000

            output_analysis["generation (GWh)"] = pd.DataFrame(
                [total_generation_by_carrier] * (analysis_period + 1),
                index=range(self.model_year, self.model_year + (analysis_period + 1))
            )

            # Group by carrier and sum capacity
            total_capacity_by_carrier = network.generators.groupby("carrier")["p_nom_opt"].sum()
            output_analysis["capacity (MW)"] = pd.DataFrame(
                [total_capacity_by_carrier] * (analysis_period + 1),
                index=range(self.model_year, self.model_year + (analysis_period + 1))
            )

            print("\nTotal Capacity Assumed by Carrier (MW):")
            print(total_capacity_by_carrier)

            # Aggregate active capacities by carrier
            active_capacity_by_carrier = network.generators.loc[active_generators.columns].groupby("carrier")[
                "p_nom_opt"].sum()
            output_analysis["active capacity by carrier (MW)"] = active_capacity_by_carrier

            print("\nActive Capacity by Carrier (MW):")
            print(active_capacity_by_carrier)

            # Aggregate active capacities by generator
            active_capacity_by_generator = network.generators.loc[active_generators.columns, ["p_nom_opt"]]
            output_analysis["active capacity by generator (MW)"] = active_capacity_by_generator

            print("\nActive Capacity by Generator (MW):")
            print(active_capacity_by_generator)

            # Adjusted capacity using peak generation by generator (only for positive dispatch)
            adjusted_capacity_by_generator = network.generators_t.p.loc[:, active_generators.columns].max()
            output_analysis["adjusted capacity by generator (MW)"] = adjusted_capacity_by_generator

            print("\nAdjusted Capacity by Generator (MW):")
            print(adjusted_capacity_by_generator)

            # Adjusted capacity using peak generation by carrier (sum of adjusted capacity by generator by carrier)
            adjusted_capacity_by_carrier = adjusted_capacity_by_generator.groupby(network.generators.carrier).sum()
            output_analysis["adjusted capacity by carrier (MW)"] = adjusted_capacity_by_carrier

            print("\nAdjusted Capacity by Carrier (MW):")
            print(adjusted_capacity_by_carrier)

            # Aggregate active capacities by carrier
            active_capacity_by_carrier = network.generators.loc[active_generators.columns].groupby("carrier")[
                "p_nom_opt"].sum()
            output_analysis["active capacity by carrier (MW)"] = active_capacity_by_carrier

            # Calculate active capacity factor
            active_capacity_factor_by_generator = generation_by_generator / (
                        active_capacity_by_generator["p_nom_opt"] * 8760)
            output_analysis["active capacity factor by generator"] = active_capacity_factor_by_generator

            active_capacity_factor_by_carrier = generation_by_generator.groupby(network.generators.carrier).sum() / (
                        active_capacity_by_carrier * 8760)
            output_analysis["active capacity factor by carrier"] = active_capacity_factor_by_carrier

            print("\nActive Capacity Factor by Generator (%):")
            print(active_capacity_factor_by_generator * 100)

            print("\nActive Capacity Factor by Carrier (%):")
            print(active_capacity_factor_by_carrier * 100)

            # Calculate adjusted capacity factor
            adjusted_capacity_factor_by_generator = generation_by_generator / (adjusted_capacity_by_generator * 8760)
            output_analysis["adjusted capacity factor by generator"] = adjusted_capacity_factor_by_generator

            adjusted_capacity_factor_by_carrier = generation_by_generator.groupby(network.generators.carrier).sum() / (
                        adjusted_capacity_by_carrier * 8760)
            output_analysis["adjusted capacity factor by carrier"] = adjusted_capacity_factor_by_carrier

            print("\nAdjusted Capacity Factor by Generator:")
            print(adjusted_capacity_factor_by_generator)

            print("\nAdjusted Capacity Factor by Carrier:")
            print(adjusted_capacity_factor_by_carrier)


            # Extract actual generation from results
            actual_generation = network.generators_t.p

            # Extract curtailment power flow
            # curtailment_flows = network.links_t.p1.filter(like="curtailment_")

            # Summarize results for each carrier
            effective_generation = actual_generation.groupby(network.generators.carrier, axis=1).sum()
            # curtailed_generation = curtailment_flows.groupby(network.links.carrier, axis=1).sum()

            # Display results
            print("\nEffective Generation (MWh):")
            print(effective_generation.sum())

            # print("\nCurtailed Generation (MWh):")
            # print(curtailed_generation.sum())

            # Store results in output_analysis for further use
            output_analysis["effective generation (MWh)"] = effective_generation
            # output_analysis["curtailed generation (MWh)"] = curtailed_generation

            # Generation share
            output_analysis["share (%)"] = output_analysis['generation (GWh)'].div(
                output_analysis['generation (GWh)'].sum(axis=1), axis=0
            )

            print("\nUsage rate (%):")
            print(generation_by_carrier.sum() / 8760 / total_capacity_by_carrier * 100)

            print("\nShare of each source (%)")
            print(generation_by_carrier.sum() / generation_by_carrier.sum().sum() * 100)
            # Calculate total marginal cost by carrier

            # Check if time-varying marginal costs are available
            if "marginal_cost" in network.generators_t:
                mc_timevarying_df = network.generators_t.marginal_cost
            else:
                # Create a DataFrame with zero marginal cost as default
                mc_timevarying_df = pd.DataFrame(
                    0.0,
                    index=network.snapshots,
                    columns=network.generators.index
                )

            mc_static_series = network.generators["marginal_cost"].fillna(0.0)
            mc_static_df = pd.DataFrame(index=network.snapshots, columns=network.generators.index)
            for gen in mc_static_series.index:
                mc_static_df[gen] = mc_static_series[gen]

            marginal_cost_df = mc_timevarying_df.add(mc_static_df, fill_value=0.0)

            # Multiply by dispatch
            gen_dispatch_df = network.generators_t.p
            gen_total_cost_series = (gen_dispatch_df * marginal_cost_df).sum(axis=0)

            # Group by carrier
            carrier_series = network.generators["carrier"]
            marginal_cost_by_carrier = gen_total_cost_series.groupby(carrier_series).sum()

            output_analysis["marginal cost (KRW)"] = pd.DataFrame(
                [marginal_cost_by_carrier] * (analysis_period + 1),
                index=range(self.model_year, self.model_year + (analysis_period + 1))
            )

            # 5) Apply the annual rate_increase to grid_electricity in the output DataFrame
            if "grid_electricity" in marginal_cost_by_carrier:
                base_cost = marginal_cost_by_carrier["grid_electricity"]
                years = range(self.model_year, self.model_year + (analysis_period + 1))

                # For each year, apply (1 + rate_increase)^(year - start_year)
                rate_adjusted_costs = {
                    year: base_cost * ((1 + self.rate_increase) ** (year - self.model_year))
                    for year in years
                }

                # Update marginal costs for grid_electricity per year
                for year, cost in rate_adjusted_costs.items():
                    output_analysis["marginal cost (KRW)"].loc[year, "grid_electricity"] = cost

            print("\nTotal Marginal Costs by Carrier (KRW)")
            print(marginal_cost_by_carrier)

            cost_per_mwh = (
                    marginal_cost_by_carrier /
                    total_generation_by_carrier.replace(0, 1e-10)
            ).astype(float).round(2)
            print("\nAmount to be paid per MWh (rounded to 2 decimal places):")
            print(cost_per_mwh)

            output_analysis["cost per MWh (KRW)"] = output_analysis["marginal cost (KRW)"].div(
                (output_analysis["generation (GWh)"] * 1000)
            )

            # output_analysis["carbon intensity (kgCO2/MWh)"] = gridinf_df.loc[self.model_year: self.end_year+1] / 1000

            # Emissions
            output_analysis["emission (tCO2)"] = (
                    output_analysis["generation (GWh)"]["grid_electricity"] * 1000 *
                    gridinf_df.loc[self.model_year: self.end_year + 1]["co2"] / 1000  # MWh * tCO2/MWh
            )

            output_analysis["carbon price (KRW)"] = (
                    output_analysis["emission (tCO2)"] *
                    carbonprice_grid["value"] *
                    ets_requirement["value"]
            )

            output_analysis["renewable share (%)"] = gridinf_df.loc[self.model_year: self.end_year + 1]['ren_share']

            # REC
            output_analysis["rec amount (REC)"] = (
                    output_analysis["generation (GWh)"]["grid_electricity"] * 1000 *
                    (1 - gridinf_df.loc[self.model_year: self.end_year + 1]['ren_share'])
            )
            output_analysis["rec payment (KRW)"] = (
                    output_analysis["rec amount (REC)"] *
                    rec_grid["value"]
            )

            output_analysis["total payment (KRW)"] = (
                    output_analysis["marginal cost (KRW)"].sum(axis=1) +
                    output_analysis["rec payment (KRW)"] +
                    output_analysis["carbon price (KRW)"]
            )

            print(f"\nTotal payment without Battery (Billion KRW in {self.model_year}):")
            print(output_analysis["total payment (KRW)"].iloc[0] / 1e9)

            print(f"\nPayment without Battery (KRW/MWh in {self.model_year}):")
            print(output_analysis["total payment (KRW)"].iloc[0] / output_analysis['generation (GWh)'].loc[
                self.model_year].sum() / 1000)

            # Plot results
            generation_by_carrier = generation_by_carrier[['agriPV', 'PV', 'offshore_wind', 'grid_electricity']]
            non_zero_generation = generation_by_carrier.loc[:, generation_by_carrier.sum() > 0]

            output_analysis["generation by carrier (MWh)"] = generation_by_carrier

            if self.battery_parameters['include']:
                # Get optimized battery capacity (MW)
                battery_capacity = network.storage_units.at["Battery", "p_nom_opt"]

                # Compute total capital cost of battery (KRW)
                battery_capital_cost = battery_capacity * self.battery_parameters["capital_cost_per_mw"]

                # Define discount rate and lifetime for levelized cost calculation
                discount_rate = self.default_params["discount_rate"]
                battery_lifetime = self.battery_parameters["lifespan"]

                # Compute annuity factor
                annuity_factor = discount_rate / (1 - (1 + discount_rate) ** -battery_lifetime)

                # Compute annualized battery cost (KRW per year)
                annualized_battery_cost = battery_capital_cost * annuity_factor

                # Define year range for analysis
                years = list(range(self.model_year, self.end_year + 1))

                # Create DataFrame for ESS capacity
                output_analysis["ESS capacity (MW)"] = pd.DataFrame({
                    "year": years,
                    "Value": battery_capacity  # Broadcast value over all years
                }).set_index("year")  # Ensure "year" is the index

                # Create DataFrame for annualized ESS cost
                output_analysis["ESS annualized cost (KRW per y)"] = pd.DataFrame({
                    "year": years,
                    "Value": annualized_battery_cost  # Broadcast value over all years
                }).set_index("year")  # Ensure "year" is the index

                # Print to verify correctness
                print("\nCorrected ESS Capacity (MW):")
                print(output_analysis["ESS capacity (MW)"])

                print("\nCorrected ESS Annualized Cost (KRW per y):")
                print(output_analysis["ESS annualized cost (KRW per y)"])

                # -------- Retrieve battery charge/discharge --------
                battery_p = network.storage_units_t.p["Battery"]
                battery_discharge_hourly = battery_p.clip(lower=0)
                battery_charge_hourly = battery_p.clip(upper=0).abs()

                battery_discharge_annual = battery_discharge_hourly.sum()
                battery_charge_annual = battery_charge_hourly.sum()

                # Existing prints
                print("\nHourly ESS discharge (MW) head:")
                print(battery_discharge_hourly.head())
                print("\nHourly ESS charge (MW) head:")
                print(battery_charge_hourly.head())
                print(f"\nTotal ESS discharge (MWh): {battery_discharge_annual:.2f}")
                print(f"Total ESS charge (MWh): {battery_charge_annual:.2f}")

                # -------- Store annual results in 'year'/'value' format --------

                # Retrieve battery charge/discharge data for all years
                battery_p = network.storage_units_t.p["Battery"]
                battery_discharge_hourly = battery_p.clip(lower=0)  # Discharge (positive values)
                battery_charge_hourly = battery_p.clip(upper=0).abs()  # Charge (negative values made positive)

                # Resample to get yearly sum for all years in the dataset
                battery_discharge_annual = battery_discharge_hourly.resample('Y').sum()
                battery_charge_annual = battery_charge_hourly.resample('Y').sum()

                # Convert index to years and store as DataFrame
                discharge_annual_df = pd.DataFrame({
                    "year": battery_discharge_annual.index.year,
                    "Value": battery_discharge_annual.values
                }).set_index("year")  # Ensure "year" is the index

                charge_annual_df = pd.DataFrame({
                    "year": battery_charge_annual.index.year,
                    "Value": battery_charge_annual.values
                }).set_index("year")  # Ensure "year" is the index

                # Store corrected annual results
                output_analysis["annual discharge (MWh)"] = discharge_annual_df
                output_analysis["annual charge (MWh)"] = charge_annual_df

                # Print to verify correctness
                print("\nCorrected Annual ESS Discharge (MWh):")
                print(discharge_annual_df)

                print("\nCorrected Annual ESS Charge (MWh):")
                print(charge_annual_df)

            # Ensure all output_analysis DataFrames have 'year' as index name if it's empty
            for key, df in output_analysis.items():
                if isinstance(df, pd.DataFrame) and df.index.name is None:
                    df.index.name = "year"

        else:
            output_analysis = "Infeasible"

        return output_analysis