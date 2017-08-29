"""
Test Engine
"""
import inspect
import itertools
import random
import contextlib
from collections import OrderedDict
import copy

import core
from utils import pretty
from log import get_logger, prefix_logger, get_file_logger, make_timing_logger
from algorithms import route_permutations
from case import Case

LOGGER = get_logger(__name__)
time_log = make_timing_logger(LOGGER)


# TODO: more offical
def get_name(obj):
    if getattr(obj, '__name__', None):
        return obj.__name__
    else:
        return obj.__class__.__name__


class Engine(object):
    def __init__(self, modules, doc_modules):
        self.modules = modules
        self.doc_modules = doc_modules
        self.checkpoints = core.Container()
        self.actions = core.Container()
        self.hybrids = core.Container()
        self.grafts = core.Container()
        # TODO: not use dict
        self.doc_funcs = {}
        self.env = core.Env()
        self.params = core.Params()
        self.dep_map = None

        def _handle_func(func, conatiner):
            if core.is_TestObject(func):
                inst_func = func()
                conatiner.add(inst_func)
            else:
                conatiner.add(func)

        for module in modules:
            for _, func in inspect.getmembers(module, core.is_Action):
                # TODO: remove dup code
                _handle_func(func, self.actions)
            for _, func in inspect.getmembers(module, core.is_CheckPoint):
                _handle_func(func, self.checkpoints)
            for _, func in inspect.getmembers(module, core.is_Hybrid):
                _handle_func(func, self.hybrids)

            for _, func in inspect.getmembers(module, core.is_Graft):
                self.grafts |= core.Container(core.get_all_depend(func, depend_cls=core.Graft))

        for func in self.all_funcs:
            for module in self.doc_modules:
                name = get_name(func)
                if getattr(module, name, None):
                    self.doc_funcs[name] = getattr(module, name)
                    break

    def run(self):
        """
        need implement
        """
        raise NotImplementedError

    def compute_depend_items(self, func):
        """
        broken and not been used
        """
        pos_items = []
        requires = core.get_all_depend(func, depend_cls=core.Consumer)
        # print "requires of func %s : %s" % (func, requires)
        for require in requires:
            if self.env.hit_require(require):
                continue
            # print "try to find some func to fit require"
            funcs = self.get_all_depend_provider(require)
            cases = []
            for func in funcs:
                cases.extend(self.compute_depend_items(func))
            pos_items.append(cases)

        if not pos_items: 
            return [func]
        return list(itertools.product(*pos_items))

    def compute_steps_order(self, items):
        pass

    def compute_full_steps(self, steps):
        pass

    def find_suit_envs(self, env, dep=None):
        if not self.dep_map:
            raise Exception('Need gen depend map first')
        for key_env in self.dep_map.keys():
            if env <= key_env:
                if dep and (len(key_env) - len(env)) > dep:
                    continue
                yield key_env

    def compute_route_permutations(self, target_env, cleanup=False, src_env=None):
        if not self.dep_map:
            raise Exception('Need gen depend map first')
        base_env = src_env if src_env else self.env
        if cleanup:
            routes = route_permutations(self.dep_map, target_env, base_env)
        else:
            routes = route_permutations(self.dep_map, base_env, target_env)
        ret_routes = []
        for route in routes:
            ret_routes.extend(itertools.product(*route))
        return ret_routes

    @property
    def all_funcs(self):
        return self.hybrids | self.checkpoints | self.actions

    def _cb_filter_with_param(self, func):
        param_req = core.get_func_params_require(func)
        if not param_req:
            return False
        if not self.params >= param_req.param_depend:
            return True
        else:
            return False

    def filter_all_func_custom(self, cb):
        self.filter_func_custom(self.actions, cb)
        self.filter_func_custom(self.checkpoints, cb)
        self.filter_func_custom(self.hybrids, cb)

    def filter_func_custom(self, container, cb):
        need_remove = core.Container()
        for func in container:
            if cb(func):
                LOGGER.debug('Remove func ' + str(func))
                need_remove.add(func)
        container -= need_remove

    def get_all_depend_provider(self, depend):
        providers = []
        if depend.type == core.Consumer.REQUIRE:
            req_types = [core.Provider.SET]
        elif depend.type == core.Consumer.REQUIRE_N:
            req_types = [core.Provider.CLEAR]
        else:
            return providers
        for func in self.actions:
            tmp_depends = core.get_all_depend(func, req_types, ret_list=False)
            if depend.env_depend in tmp_depends.keys():
                providers.append(func)
        return providers

    def find_checkpoints(self):
        ret = []
        for func in self.checkpoints:
            requires = core.get_all_depend(func, depend_cls=core.Consumer)
            if self.env.hit_requires(requires):
                ret.append(func)
        return ret

    def gen_depend_map(self):
        requires = core.Container()
        for func in self.actions|self.hybrids:
            tmp_requires = core.get_all_depend(func, [core.Provider.SET], depend_cls=core.Provider)
            requires |= core.Container(tmp_requires)

        new_requires = core.Container()
        for graft in self.grafts:
            for require in requires:
                new_req = graft.gen_trans_depend(require)
                if new_req:
                    new_requires.add(new_req)
        requires |= new_requires

        nodes = []
        dep_map = {}
        for i in range(len(requires) + 1):
            nodes.extend(itertools.combinations(requires, i))

        for node in nodes:
            tmp_e = core.Env()
            tmp_e.call_effect_env(node)
            dep_map[tmp_e] = {}

        for func in self.actions|self.hybrids:
            for env_key in dep_map.keys():
                tmp_e = env_key.gen_transfer_env(func)
                if tmp_e is None:
                    continue
                data = dep_map[env_key]
                data.setdefault(tmp_e, []).append(func)
        LOGGER.debug(pretty(dep_map))
        LOGGER.info('Depend map is %d x %d size',
                    len(dep_map), len(dep_map))
        self.dep_map = dep_map

    def replace_depend_with_param(self, depend):
        pass

    def get_all_depend_consumer(self, depend):
        """
        Not been used
        """
        consumers = []
        if depend.type == core.Provider.SET:
            req_types = [core.Consumer.REQUIRE]
        elif depend.type == core.Provider.CLEAR:
            req_types = [core.Consumer.REQUIRE_N]
        else:
            return consumers
        for func in self.actions:
            tmp_depends = core.get_all_depend(func, req_types, ret_list=False)
            if depend.env_depend in tmp_depends.keys():
                consumers.add(func)
        return consumers

    def full_logger(self, msg):
        #TODO
        LOGGER.info(msg)
        self.params.doc_logger.info(msg)

    def _check_mists(self, mists, env, func):
        # TODO support mutli mist
        if not mists:
            return
        for mist in mists:
            name = mist.reach(env, func)
            if name:
                return name, mist

    def gen_one_step_doc(self, func, step_index=None, check=False, mists=None):
        if getattr(func, '__name__', None):
            doc_func_name = func.__name__
        else:
            doc_func_name = func.__class__.__name__

        if step_index is not None:
            #TODO: move doc_logger definition in basic engine
            self.params.doc_logger.info('%d.\n' % step_index)
            step_index += 1

        if doc_func_name not in self.doc_funcs.keys():
            self.params.doc_logger.info("Not define %s name in doc modules" % doc_func_name)
        doc_func = self.doc_funcs[doc_func_name]

        mist = self._check_mists(mists, self.env, func)
        LOGGER.debug("Func %s mist %s", doc_func, mist)
        LOGGER.debug("core.Env: %s", self.env)
        if mist:
            name, mist_func = mist
            if mist_func.__doc__:
                self.params.doc_logger.info("Desciption: %s" % mist_func.__doc__)
            # TODO: here will raise a exception which require the caller handle this
            try:
                mist_func(name, doc_func, self.params, self.env)
            except core.MistClearException:
                mists.remove(mist_func)
            # TODO: mist in the mist
            ret = None
        else:
            if doc_func.__doc__:
                self.params.doc_logger.info("Desciption: %s" % doc_func.__doc__)

            ret = doc_func(self.params, self.env)

        LOGGER.debug("Start transfer env, func: %s env: %s", func, self.env)
        new_env = self.env.gen_transfer_env(func)
        if new_env is None:
            import pdb; pdb.set_trace()
            raise Exception("Fail to gen transfer env")

        LOGGER.debug("core.Env transfer to %s", new_env)
        self.env = new_env

        if ret and mists is not None:
            LOGGER.debug('Add a new mist %s', ret)
            mists.append(ret)

        if not check:
            return step_index

        checkpoints = self.find_checkpoints()
        for i, checkpoint in enumerate(checkpoints):
            if checkpoint == func:
                continue
            step_index = self.gen_one_step_doc(checkpoint,
                step_index=step_index, mists=mists)

        return step_index

    def run_one_step(self, func, check=True, doc=False):
        # TODO: merge this method and find_all_way_to_target to one method
        if core.is_TestObject(func):
            func = func()

        if func.__doc__:
            self.full_logger("Desciption: %s" % func.__doc__)
        func(self.params, self.env)
        self.env = self.env.gen_transfer_env(func)
        LOGGER.debug("core.Env: %s, func: %s", self.env, func)
        if not check:
            return
        checkpoints = self.find_checkpoints()
        for i, checkpoint in enumerate(checkpoints):
            if checkpoint.__doc__:
                self.full_logger("Desciption: %s" % checkpoint.__doc__)
            checkpoint(self.params, self.env)

    def find_all_way_to_target(self, target_env, random_cleanup=True):
        for tgt_env in self.find_suit_envs(target_env, 2):
            cases = self.compute_route_permutations(tgt_env)
            cleanups = self.compute_route_permutations(tgt_env, True)
            if cleanups:
                if random_cleanup:
                    cleanup_steps = random.choice(cleanups)
                else:
                    cleanup_steps = min(cleanups)
            else:
                cleanup_steps = None

            LOGGER.debug("env: %s case num: %d" % (tgt_env, len(cases)))
            for case in cases:
                case_obj = Case(case, tgt_env=tgt_env,
                                cleanups=cleanup_steps)
                yield case_obj

    def find_mist_routes(self, mist, src_env=None):
        routes = []
        if src_env is None:
            src_env = self.env

        for name, data in mist._areas.items():
            start_env, end_env = data
            for tgt_start_env in self.find_suit_envs(start_env, 2):
                if tgt_start_env == src_env:
                    cases = None
                else:
                    cases = self.compute_route_permutations(tgt_start_env, src_env=src_env)
                    if not cases:
                        continue
                for tgt_end_env in self.dep_map[tgt_start_env].keys():
                    if end_env <= tgt_end_env:
                        funcs = self.dep_map[tgt_start_env][tgt_end_env]
                        if cases:
                            for data in itertools.product(cases, funcs):
                                case = list(data[0])
                                case.append(data[1])
                                case_obj = Case(case, tgt_env=tgt_end_env)
                                yield case_obj
                        else:
                            for func in funcs:
                                case_obj = Case([func], tgt_env=tgt_end_env)
                                yield case_obj

    def gen_mist_cases(self, mist, old_case, src_env=None, test_func=None):
        # Here we get a new mist in the test func
        # we need to trigger this mist
        if not src_env:
            src_env = self.env
        extra_steps = list(self.find_mist_routes(mist, src_env))
        history_steps = Case(list(old_case.steps), tgt_env=old_case.tgt_env, cleanups=old_case.cleanups)
        if test_func:
            history_steps.append(test_func)
        for extra_step in extra_steps:
            new_case = history_steps + extra_step
            LOGGER.debug("create a new case: %s", list(new_case.steps))
            LOGGER.debug("history steps: %s, extra_step: %s", list(history_steps.steps), list(extra_step.steps))
            yield new_case

    def run_case(self, case, case_id, test_func=None, need_cleanup=None):
        step_index = 1
        mists = []
        extra_cases = []
        self.full_logger("=" * 8 + " case %d " % case_id + "=" * 8)
        try:
            if test_func:
                test_func = test_func
                steps = list(case.steps)
            else:
                steps = list(case.steps)
                test_func = steps[-1]
                steps = steps[:-1]

            for func in steps:
                # TODO support real case
                step_index = self.gen_one_step_doc(func,
                    step_index=step_index, mists=mists)

            old_mists = len(mists)
            step_index = self.gen_one_step_doc(test_func,
                    step_index=step_index, check=self.params.extra_check, mists=mists)

            if len(mists) - old_mists > 0:
                extra_cases = list(self.gen_mist_cases(mists[-1], case, self.env, test_func))
        except core.MistDeadEndException:
            # TODO: maybe need clean up
            pass
        else:
            if need_cleanup:
                if not case.cleanups:
                    LOGGER.info("Cannot find clean up way")
                    LOGGER.info("Current core.Env: %s", self.env)
                else:
                    for func in case.clean_ups:
                        step_index = self.run_one_step_doc(func, step_index=step_index)
        # TODO: remove this
        self.env = core.Env()
        return extra_cases


class Template(Engine):
    pass

class StaticTemplate(Template):
    pass

class MatrixTemplate(Template):
    pass

class Fuzz(Engine):
    pass

class AI(Engine):
    pass


class Demo(Engine):
    def __init__(self, basic_modules, test_modules=None,
                 test_funcs=None, doc_modules=None):
        self.test_modules = test_modules
        self.test_funcs = test_funcs
        tmp_modules = []
        tmp_modules.extend(basic_modules)
        if self.test_modules:
            tmp_modules.extend(test_modules)
        super(Demo, self).__init__(tmp_modules, doc_modules)

    def _prepare_test_funcs(self):
        if not self.test_modules and not self.test_funcs:
            raise Exception('Need give a test object !')
        if self.test_modules:
            test_funcs = []
            for module in self.test_modules:
                for _, func in inspect.getmembers(module, (core.is_Action, core.is_Hybrid)):
                    test_funcs.add(func)
        else:
            test_funcs = self.test_funcs
        return test_funcs

    def _excute_test(self, test_func):
        if getattr(test_func, 'func_name', None):
            title = getattr(test_func, 'func_name')
        else:
            title = str(test_func)

        self.full_logger("=" * 8 + " %s " % title + "=" * 8)
        self.full_logger("")
        target_env = core.Env.gen_require_env(test_func)
        i = 1
        for tgt_env in self.find_suit_envs(target_env, 2):
            cases = self.compute_route_permutations(tgt_env)
            cleanup = self.compute_route_permutations(tgt_env, True)
            for case in cases:
                # TODO
                self.full_logger("=" * 8 + " case %d " % i + "=" * 8)
                for func in case:
                    self.run_one_step(func)
                self.run_one_step(test_func)
                i += 1
                if not cleanup:
                    LOGGER.info("Cannot find clean up way")
                else:
                    cleanup_case = random.choice(cleanup)
                    for func in cleanup_case:
                        self.run_one_step(func, False)
                LOGGER.info("Current core.Env: %s", self.env)
                LOGGER.info("")
                self.env = core.Env()

    def _gen_test_case_doc(self, test_func, need_cleanup=False, full_matrix=True):
        if getattr(test_func, 'func_name', None):
            title = getattr(test_func, 'func_name')
        else:
            title = str(test_func)

        self.params.doc_logger = self.params.case_logger
        self.full_logger("=" * 8 + " %s " % title + "=" * 8)
        self.full_logger("")
        target_env = core.Env.gen_require_env(test_func)
        i = 1
        with time_log('Compute case permutations'):
            case_matrix = list(self.find_all_way_to_target(target_env))

        if not full_matrix:
            LOGGER.info('Find %d route, and use the shortest one', len(case_matrix))
            case = min(case_matrix)
            case_matrix = [case]

        extra_cases = []
        while case_matrix:
            case = case_matrix.pop()
            new_extra_cases = self.run_case(case, i, test_func, need_cleanup)
            extra_cases.extend(new_extra_cases)
            i += 1

        LOGGER.info("find another %d extra cases", len(extra_cases))
        self.params.doc_logger = self.params.mist_logger
        for extra_case in extra_cases:
            ret = self.run_case(extra_case, i, need_cleanup=need_cleanup)
            if ret:
                raise NotImplementedError
            i += 1

    @contextlib.contextmanager
    def preprare_logger(self, doc_file):
        self.params.logger = LOGGER
        doc_path = 'doc.file' if not doc_file else doc_file
        if self.params.mist_rules == 'both':
            logger = get_file_logger(doc_path, doc_path)
            self.params.case_logger = logger
            self.params.mist_logger = logger
            yield
            LOGGER.info('Write all case to %s', doc_path)
        elif self.params.mist_rules == 'split':
            mist_file = doc_path + '-mist'
            case_file = doc_path + '-case'
            self.params.mist_logger = get_file_logger(mist_file, mist_file)
            self.params.case_logger = get_file_logger(case_file, case_file)
            yield
            LOGGER.info('Write standerd case to %s', case_file)
            LOGGER.info('Write extra case to %s', mist_file)
        else:
            raise NotImplementedError

    def report_log_file(self):
        LOGGER.info('')

    def run(self, params, doc_file=None):
        self.params = params
        LOGGER.debug(self.params.pretty_display())
        # TODO
        with self.preprare_logger(doc_file):
            self.filter_all_func_custom(self._cb_filter_with_param)
            with time_log('Gen the depend map'):
                self.gen_depend_map()

            tests = []
            test_funcs = self._prepare_test_funcs()

            while test_funcs:
                # TODO
                self.env = core.Env()

                test_case = []
                order = []
                test_func = random.choice(test_funcs)
                test_funcs.remove(test_func)
                if self.params.test_case:
                    self._gen_test_case_doc(test_func, full_matrix=self.params.full_matrix)
                else:
                    self._excute_test(test_func)
