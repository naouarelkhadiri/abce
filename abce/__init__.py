
# Copyright 2012 Davoud Taghawi-Nejad
#
# Module Author: Davoud Taghawi-Nejad
#
# ABCE is open-source software. If you are using ABCE for your research you are
# requested the quote the use of this software.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License and quotation of the
# author. You may obtain a copy of the License at
#       http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
#pylint: disable=W0212, C0111
""" The best way to start creating a simulation is by copying the start.py file
and other files from 'abce/template'.

To see how to create a simulation read :doc:`Walk_through`. In this module you
will find the explanation for the command.

This is a minimal template for a start.py::

    from __future__ import division  # makes / division work correct in python !
    from agent import Agent
    from abce import *


    for parameters in read_parameters('simulation_parameters.csv'):
        s = Simulation(parameters)
        action_list = [
        ('all', 'one'),
        ('all', 'two'),
        ('all', 'three'),
        ]
        s.add_action_list(action_list)
        s.build_agents(Agent, 2)
        s.run()
"""
import csv
import datetime
import os
import time
from abce.tools import agent_name
import multiprocessing as mp
import abce.db
import abce.abcelogger
import itertools
import postprocess
from glob import glob
from firmmultitechnologies import *
from household import Household
from agent import *
from collections import defaultdict
from firm import Firm


BASEPATH = os.path.dirname(os.path.realpath(__file__))


def execute_wrapper(inp):
    return inp[0].execute_parallel(inp[1], inp[2])


def execute_internal_wrapper(inp):
    return inp[0].execute_internal(inp[1])


def read_parameters(parameters_file='simulation_parameters.csv'):
    """ reads a parameter file line by line and gives a list. Where each line
    contains all parameters for a particular run of the simulation.

    Args:

        parameters_file (optional):
            filename of the csv file. (default:`simulation_parameters.csv`)

        delimiter (optional):
            delimiter of the csv file. (default: tabs)

        quotechar (optional):
            for single entries that contain the delimiter. (default: ")
            See python csv lib http://docs.python.org/library/csv.html


    This code reads the file and runs a simulation for every line::

     for parameter in read_parameters('simulation_parameters.csv'):
        w = Simulation(parameter)
        w.build_agents(Agent, 'agent', 'num_agents')
        w.run()
    """
    start_time = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    parameter_array = []

    csvfile = open(parameters_file)
    dialect = csv.Sniffer().sniff(csvfile.read(1024))
    csvfile.seek(0)
    reader = csv.reader(csvfile, dialect)

    keys = [key.lower() for key in reader.next()]
    for line in reader:
        if line == []:
            continue
        cells = [_number_or_string(cell.lower()) for cell in line]
        parameter = dict(zip(keys, cells))
        if 'num_rounds' not in keys:
            raise SystemExit('No "num_rounds" column in ' + parameters_file)
        if 'name' not in parameter:
            try:
                parameter['name'] = parameter['Name']
            except KeyError:
                print("no 'name' (lowercase) column in " + parameters_file)
                parameter['name'] = 'abce'
        parameter['name'] = str(parameter['name']).strip("""\"""").strip("""\'""")
        try:
            if parameter['random_seed'] == 0:
                parameter['random_seed'] = None
        except KeyError:
            parameter['random_seed'] = None
        parameter['_path'] = os.path.abspath('./result/' + parameter['name'] + '_' + start_time)
        try:
            os.makedirs('./result/')
        except OSError:
            pass
        try:
            os.makedirs(parameter['_path'])
        except OSError:
            files = glob(parameter['_path'] + '/*')
            for file_to_remove in files:
                os.remove(file_to_remove)
        for key in parameter:
            if key == '' or key[0] == '#' or key[0] == '_':
                del key
        parameter_array.append(parameter)
    return parameter_array


class Simulation:
    """ This class in which the simulation is run. It takes
    the simulation_parameters to set up the simulation. Actions and agents have to be
    added. databases and resource declarations can be added. Then runs
    the simulation.

    Usually the parameters are specified in a tab separated csv file. The first
    line are column headers.

    Args::

     simulation_parameters: a dictionary with all parameters. "name" and
     "num_rounds" are mandatory.


    Example::
     for simulation_parameters in read_parameters('simulation_parameters.csv'):
        action_list = [
        ('household', 'recieve_connections'),
        ('household', 'offer_capital'),
        ('firm', 'buy_capital'),
        ('firm', 'production'),
        ('household', 'buy_product')
        'after_sales_before_consumption'
        ('household', 'consume')
        ]
        w = Simulation(simulation_parameters)
        w.add_action_list(action_list)
        w.build_agents(Firm, 'firm', 'num_firms')
        w.build_agents(Household, 'household', 'num_households')

        w.declare_round_endowment(resource='labor_endowment', productivity=1, product='labor')
        w.declare_round_endowment(resource='capital_endowment', productivity=1, product='capital')

        w.panel_data('firm', command='after_sales_before_consumption')

        w.run()
    """
    def __init__(self, simulation_parameters):
        self.simulation_parameters = simulation_parameters
        """ Simulation parameters are the parameter you specify for the current
        simulation. Either in simulation_parameters.csv or as a dictionary
        """
        self.agents_list = {}
        self.agents_list['all'] = []
        self._messages = {}
        self._action_list = []
        self._resource_command_group = {}
        self._db_commands = {}
        self.num_agents = 0
        self.num_agents_in_group = {}
        self._build_first_run = True
        self._agent_parameters = None
        self.resource_endowment = defaultdict(list)
        self.perishable = []
        self.expiring = []
        self.variables_to_track_panel = defaultdict(list)
        self.variables_to_track_aggregate = defaultdict(list)
        self.possessins_to_track_panel = defaultdict(list)
        self.possessions_to_track_aggregate = defaultdict(list)

        manager = mp.Manager()
        self.database_queue = manager.Queue()
        self._db = abce.db.Database(simulation_parameters['_path'], self.database_queue)
        self.logger_queue = manager.Queue()
        self._logger = abce.abcelogger.AbceLogger(simulation_parameters['_path'], self.logger_queue)
        self._logger.start()

        self.round = 0
        try:
            self.trade_logging_mode = simulation_parameters['trade_logging'].lower()
        except KeyError:
            self.trade_logging_mode = 'individual'
            print("'trade_logging' in simulation_parameters.csv not set"
                  ", default to 'individual', possible values "
                  "('group' (fast) or 'individual' (slow) or 'off')")
        if not(self.trade_logging_mode in ['individual', 'group', 'off']):
            print(type(self.trade_logging_mode), self.trade_logging_mode, 'error')
            SystemExit("'trade_logging' in simulation_parameters.csv can be "
                       "'group' (fast) or 'individual' (slow) or 'off'"
                       ">" + self.trade_logging_mode + "< not accepted")
        assert self.trade_logging_mode in ['individual', 'group', 'off']

    def add_action_list(self, action_list):
        """ add an `action_list`, which is a list of either:

        - tuples of an goup_names and and action
        - a single command string for panel_data or follow_agent
        - [tuples of an agent name and and action, currently not unit tested!]


        Example::

         action_list = [
            repeat([
                    ('Firm', 'sell'),
                    ('Household', 'buy')
                ],
                repetitions=10
            ),
            ('Household_03', 'dance')
            'panel_data_end_of_round_befor consumption',
            ('Household', 'consume'),
            ]
         w.add_action_list(action_list)
        """
        self.action_list = action_list

    def add_action_list_from_file(self, parameter):
        """ The action list can also be declared in the simulation_parameters.csv
        file. Which allows you to run a batch of simulations with different
        orders. In simulation_parameters.csv there must be a column with which
        contains the a declaration of the action lists:

        +-------------+-------------+--------------------------------------------+-----------+
        | num_rounds  | num_agents  | action_list                                | endowment |
        +=============+=============+============================================+===========+
        | 1000,       | 10,         | [ ('firm', 'sell'), ('household', 'buy')], | (5,5)     |
        +-------------+-------------+--------------------------------------------+-----------+
        | 1000,       | 10,         | [ ('firm', 'buf'), ('household', 'sell')], | (5,5)     |
        +-------------+-------------+--------------------------------------------+-----------+
        | 1000,       | 10,         | [ ('firm', 'sell'),                        |           |
        |             |             | ('household', 'calculate_net_wealth'),     |           |
        |             |             | ('household', 'buy')],                     | (5,5)     |
        +-------------+-------------+--------------------------------------------+-----------+

        The command::

            self.add_action_list_from_file('parameters['action_list'])

        Args::

            parameter
                a string that contains the action_list. The string can be read
                from the simulation_parameters file: parameters['action_list'], where action_list
                is the column header in simulation_parameters.


        """
        self.add_action_list(eval(parameter))
        # TODO test

    def declare_round_endowment(self, resource, units, product, groups=['all']):
        """ At the beginning of very round the agent gets 'units' units of good 'product' for
        every 'resource' he possesses.

        Round endowments can be group specific, that means that when somebody except
        this group holds them they do not produce. The default is 'all'.

        Args::

            resource:
                The good that you have to hold to get the other
            units:
                the multiplier to get the produced good
            product:
                the good that is produced if you hold the first good
            groups:
                a list of agent groups, which gain the second good, if they hold the first one

        Example::

            A farmer gets a ton of harvest for every acre:

            w.declare_round_endowment(resource='land', units=1000, product='wheat')


        """
        if len(self.agents_list['all']) > 0:
            raise SystemExit("WARNING: agents build before declare_round_endowment")
        for group in groups:
            self.resource_endowment[group].append((resource, units, product))

    def declare_perishable(self, good):
        """ This good only lasts one round and then disappears. For example
        labor, if the labor is not used today today's labor is lost.
        In combination with resource this is useful to model labor or capital.

        In the example below a worker has an endowment of labor and capital.
        Every round he can sell his labor service and rent his capital. If
        he does not the labor service for this round and the rent is lost.

        Args::

         good:
            the good that perishes

         Example::

             w.declare_perishable(good='LAB')
             w.declare_perishable(good='CAP')

        """
        if len(self.agents_list['all']) > 0:
            raise SystemExit("WARNING: agents build before declare_perishable")
        self.perishable.append(good)

    def declare_expiring(self, good, duration):
        """ This type of good lasts for several rounds, but eventually
        expires. For example computers would last for several years and than
        become obsolete.
        The duration can be accessed in self.simulation_parameters[good].

        Args:

            good:
                the good, which expires
            duration:
                the duration before the good expires
        """
        if len(self.agents_list['all']) > 0:
            raise SystemExit("WARNING: agents build before declare_expiring")
        self.expiring.append((good, duration))
        self.simulation_parameters[good] = duration

    def declare_service(self, human_or_other_resource, units, service, groups=['all']):
        """ When the agent holds the human_or_other_resource, he gets 'units' of service every round
            the service can be used only with in this round.

        Args::

            human_or_other_resource:
                the good that needs to be in possessions to create the other good 'self.create('adult', 2)'
            units:
                how many units of the service is available
            service:
                the service that is created
            groups:
                a list of agent groups that can create the service

        Example::

            For example if a household has two adult family members, it gets 16 hours of work

            w.declare_service('adult', 8, 'work')
        """
        self.declare_round_endowment(human_or_other_resource, units, service, groups)
        self.declare_perishable(service)

    def panel(self, group, possessions=[], variables=[]):
        """ panel(.) writes possessions and variables of a group of
            agents into the database.
            You must put ('agent_group', 'panel') in the action_list.

            Args:
                group:
                    can be either a group or 'all' for all agents
                possessions (list, optional):
                    a list of all possessions you want to track as 'strings'
                variables (list, optional):
                    a list of all variables you want to track as 'strings'

        Example in start.py::

         w.panel(group='firm', possessions=['money', 'input1'],
            variables=['production_target', 'gross_revenue'])
        """
        if len(self.agents_list['all']) > 0:
            raise SystemExit("WARNING: agents build before panel")
        self._db.add_panel(group)
        self.variables_to_track_panel[group] = variables
        self.possessins_to_track_panel[group] = possessions

    def aggregate(self, group, possessions=[], variables=[]):
        """ aggregate(.) writes summary statistics of variables and possessions
            of a group of agents into the database.
            You must put ('agent_group', 'panel') in the action_list.

            Args:
                group:
                    can be either a group or 'all' for all agents
                possessions (list, optional):
                    a list of all possessions you want to track as 'strings'
                variables (list, optional):
                    a list of all variables you want to track as 'strings'

        Example in start.py::

         w.aggregate(group='firm', possessions=['money', 'input1'],
            variables=['production_target', 'gross_revenue'])
        """
        if len(self.agents_list['all']) > 0:
            raise SystemExit("WARNING: agents build before aggregate")
        self._db.add_aggregate(group)
        self.variables_to_track_aggregate[group] = variables
        self.possessions_to_track_aggregate[group] = possessions

    def _process_action_list(self, action_list):
        processed_list = []
        for action in action_list:
            if isinstance(action, repeat):
                nested_action_list = self._process_action_list(action.action_list)
                for _ in range(action.repetitions):
                    processed_list.extend(nested_action_list)
            else:
                if not isinstance(action[0], tuple) and not isinstance(action[0], list):
                    try:
                        action = ((action[0],), action[1], action[2])
                    except IndexError:
                        action = ((action[0],), action[1])
                for group in action[0]:
                    assert group in self.num_agents_in_group.keys(), \
                        '%s in (%s, %s) in the action_list is not a known agent' % (action[0], action[0], action[1])
                if len(action) == 2:
                    processed_list.append((self.execute_serial, action[0], action[1]))
                elif len(action) == 3:
                    if action[2] == 'serial':
                        processed_list.append((self.execute_serial, action[0], action[1]))
                    elif action[2] == 'parallel':
                        processed_list.append((self.execute_parallel, action[0], action[1]))
                    else:
                        raise SystemExit("%s in %s in action_list not recognized, must be 'serial' or 'parallel' " % (action[2], action))

                else:
                    raise SystemExit("%s in action_list not recognized" % action)
        return processed_list

    def execute_parallel(self, groups, command, messagess):
        ret = []
        for group in groups:
            parameters = [(agent, command, messagess[agent.group][agent.idn]) for agent in self.agents_list[group]]
            self.agents_list[group] = self.pool.map(execute_wrapper, parameters)
            del self.agents_list['all']
            self.agents_list['all'] = [agent for agent in itertools.chain(*self.agents_list.values())]
            for agent in self.agents_list[group]:
                del messagess[agent.group][agent.idn][:]
            ret.extend([agent._out for agent in self.agents_list[group]])
        return ret

    def execute_serial(self, groups, command, messagess):
        ret = []
        for group in groups:
            ret.extend([agent.execute(command, messagess[agent.group][agent.idn]) for agent in self.agents_list[group]])
        return ret
        # message inbox deleted by the agent itself

    def execute_internal(self, group, command):
        for agent in self.agents_list[group]:
            agent.execute_internal(command)

    def run(self, parallel=False):
        """ This runs the simulation

            Args:
                parallel (False (default), True):
                    Whether the agents' utility functions are executed in parallel.
                    For simulation that don't have hundreds of separate perishable
                    goods or resource False is faster.
        """
        self.pool = mp.Pool()
        self._db.start()
        if not(self.agents_list):
            raise SystemExit('No Agents Created')
        if not(self.action_list) and not(self._action_list):
            raise SystemExit('No action_list declared')
        if not(self._action_list):
            self._action_list = self._process_action_list(self.action_list)

        self._write_description_file()
        self._displaydescribtion()

        start_time = time.time()

        messagess = self._messages

        for year in xrange(self.simulation_parameters['num_rounds']):
            self.round = year
            if year % 25 == 0:
                print("\rRound" + str("%3d" % year))
            self.execute_internal('all', '_produce_resource')

            for processor, group, action in self._action_list:
                new_messages = processor(group, action, messagess)
                messagess = sortmessages(messagess, new_messages)

            self.execute_internal('all', '_advance_round')
            self.execute_internal('all', '_perish')

        print(str("time only simulation %6.2f" % (time.time() - start_time)))
        self.gracefull_exit()
        print(str("time with data and network %6.2f" % (time.time() - start_time)))
        postprocess.to_csv(os.path.abspath(self.simulation_parameters['_path']))
        print(str("time with postprocessing %6.2f" % (time.time() - start_time)))


    def gracefull_exit(self):
        self.database_queue.put('close')
        self.logger_queue.put(['close', 'close', 'close'])
        while self._db.is_alive() or self._logger.is_alive():
            time.sleep(0.05)
        try:
            self.pool.close()
            self.pool.join()
        except AttributeError:
            pass

    def build_agents(self, AgentClass, number=None, group_name=None, agent_parameters=None):
        """ This method creates agents, the first parameter is the agent class.
        "num_agent_class" (e.G. "num_firm") should be difined in
        simulation_parameters.csv. Alternatively you can also specify number = 1.s

        Args::

         AgentClass:
            is the name of the AgentClass that you imported
        number (optional):
            number of agents to be created. or the colum name
            of the row in simulation_parameters.csv that contains this number. If not
            specified the column name is assumed to be 'num_' + agent_name
            (all lowercase). For example num_firm, if the class is called
            Firm or name = Firm.
         group_name (optional):
            to give the group a different name than the
            class_name.
        agent_parameters:
            a dictionary of agent parameters to be given to the agent

        Example::

         w.build_agents(Firm, number='num_firms')
         # 'num_firms' is a column in simulation_parameters.csv
         w.build_agents(Bank, 1)
         w.build_agents(CentralBank, number=1)
        """
        # TODO single agent groups get extra name without number
        # TODO when there is a group with a single agent the ask_agent has a confusingname
        if not(group_name):
            group_name = AgentClass.__name__.lower()
        if number and not(agent_parameters):
            try:
                num_agents_this_group = int(number)
            except ValueError:
                try:
                    num_agents_this_group = self.simulation_parameters[number]
                except KeyError:
                    SystemExit('build_agents ' + group_name + ': ' + number +
                               ' is not a number or a column name in simulation_parameters.csv'
                               'or the parameterfile you choose')
        elif not(number) and not(agent_parameters):
            try:
                num_agents_this_group = self.simulation_parameters['num_' + group_name.lower()]
            except KeyError:
                raise SystemExit('num_' + group_name.lower() + ' is not in simulation_parameters.csv')
        elif not(number) and agent_parameters:
            num_agents_this_group = len(agent_parameters)
        else:
            raise SystemExit('build_agents ' + group_name + ': Either '
                             'number_or_parameter_column or agent_parameters must be'
                             'specied, NOT both.')
        if not(agent_parameters):
            agent_parameters = [None] * num_agents_this_group

        self.simulation_parameters['num_' + group_name.lower()] = num_agents_this_group

        self.num_agents += num_agents_this_group
        self.num_agents_in_group[group_name] = num_agents_this_group
        self.num_agents_in_group['all'] = self.num_agents
        self.agents_list[group_name] = []

        for idn in range(num_agents_this_group):
            agent = AgentClass(simulation_parameters=self.simulation_parameters,
                               agent_parameters=agent_parameters[idn],
                               name=agent_name(group_name, idn),
                               idn=idn,
                               group=group_name,
                               trade_logging=self.trade_logging_mode,
                               database=self.database_queue,
                               logger=self.logger_queue)
            agent.init(self.simulation_parameters, agent_parameters)

            for good in self.perishable:
                agent._register_perish(good)
            for resource, units, product in self.resource_endowment[group_name] + self.resource_endowment['all']:
                agent._register_resource(resource, units, product)

            agent._register_panel(self.possessins_to_track_panel[group_name],
                                  self.variables_to_track_panel[group_name])

            agent._register_aggregate(self.possessions_to_track_aggregate[group_name],
                                      self.variables_to_track_aggregate[group_name])

            for good, duration in self.expiring:
                agent._declare_expiring(good, duration)

            self.agents_list[group_name].append(agent)
            self.agents_list['all'].append(agent)
        self._messages[group_name] = tuple([] for _ in range(num_agents_this_group))

    def build_agents_from_file(self, AgentClass, parameters_file=None, multiply=1):
        """ This command builds agents of the class AgentClass from an csv file.
        This way you can build agents and give every single one different
        parameters.

        The file must be tab separated. The first line contains the column
        headers. The first column "agent_class" specifies the agent_class. The
        second column "number" (optional) allows you to create more than one
        agent of this type. The other columns are parameters that you can
        access in own_parameters the __init__ function of the agent.

        Agent created from a csv-file::

         class Agent(AgentEngine):
            def __init__(self, simulation_parameter, own_parameters, _pass_to_engine):
                AgentEngine.__init__(self, *_pass_to_engine)
                self.size = own_parameters['firm_size']
        """
        # TODO declare all self.simulation_parameters['num_XXXXX'], when this is called the first time
        if parameters_file is None:
            parameters_file = self.simulation_parameters.get('agent_parameters_file', 'agent_parameters.csv')
        elif self._agent_parameters is None:
            if parameters_file != self._agent_parameters:
                SystemExit('All agents must be declared in the same agent_parameters.csv file')
        self._agent_parameters = parameters_file

        agent_class = AgentClass.__name__.lower()
        agent_parameters = []
        csvfile = open(parameters_file)
        dialect = csv.Sniffer().sniff(csvfile.read(1024))
        csvfile.seek(0)
        agent_file = csv.reader(csvfile, dialect)
        keys = [key for key in agent_file.next()]
        if not(set(('agent_class', 'number')).issubset(keys)):
            SystemExit(parameters_file + " does not have a column 'agent_class'"
                       "and/or 'number'")

        agents_list = []
        for line in agent_file:
            cells = [_number_or_string(cell) for cell in line]
            agents_list.append(dict(zip(keys, cells)))

        if self._build_first_run:
            for line in agents_list:
                num_entry = 'num_' + line['agent_class'].lower()
                if num_entry not in self.simulation_parameters:
                    self.simulation_parameters[num_entry] = 0
                self.simulation_parameters[num_entry] += int(line['number'])
            self._build_first_run = False

        for line in agents_list:
            if line['agent_class'] == agent_class:
                agent_parameters.extend([line] * line['number'] * multiply)

        self.build_agents(AgentClass, agent_parameters=agent_parameters)

    def _write_description_file(self):
        description = open(os.path.abspath(self.simulation_parameters['_path'] + '/description.txt'), 'w')
        description.write('\n\n')
        for key in self.simulation_parameters:
            description.write(key + ": " + str(self.simulation_parameters[key]) + '\n')

    def _displaydescribtion(self):
        description = open(self.simulation_parameters['_path'] + '/description.txt', 'r')
        print(description.read())


class repeat:
    """ Repeats the contained list of actions several times

    Args:

     action_list:
        action_list that is repeated

     repetitions:
        the number of times that an actionlist is repeated or the name of
        the corresponding parameter in simulation_parameters.csv

    Example with number of repetitions in simulation_parameters.csv::

        action_list = [
            repeat([
                    ('firm', 'sell'),
                    ('household', 'buy')
                ],
                repetitions=parameter['number_of_trade_repetitions']
            ),
            ('household_03', 'dance')
            'panel_data_end_of_round_before consumption',
            ('household', 'consume'),
            ]
        s.add_action_list(action_list)

    """
    def __init__(self, action_list, repetitions):
        self.action_list = action_list
        self.repetitions = repetitions


def sortmessages(messagess, new_messages):
    for messages in new_messages:
        for group, idn, message in messages:
            messagess[group][idn].append(message)
    return messagess


def _number_or_string(word):
    """ returns a int if possible otherwise a float from a string
    """
    try:
        return int(word)
    except ValueError:
        try:
            return float(word)
        except ValueError:
            return word
