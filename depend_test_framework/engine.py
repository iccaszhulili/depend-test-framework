"""
Test Engine
"""
import inspect
import itertools
import random
import contextlib
import copy
from collections import OrderedDict
from progressbar import ProgressBar, SimpleProgress, Counter, Timer

from env import Env
from base_class import Container, Params, get_func_params_require
from test_object import is_TestObject, is_Action, is_CheckPoint, is_Hybrid, MistDeadEndException
from dependency import is_Graft, is_Cut, get_all_depend, Provider, Consumer, Graft
from log import get_logger, get_file_logger, make_timing_logger
from case_generator import DependGraphCaseGenerator
from runner_handlers import MistsHandler
from runners import Runner

LOGGER = get_logger(__name__)
time_log = make_timing_logger(LOGGER)


# TODO: more offical
def get_name(obj):
    if getattr(obj, '__name__', None):
        return obj.__name__
    else:
        return obj.__class__.__name__


class BaseEngine(object):
    def __init__(self, modules, doc_modules):
        self.modules = modules
        self.doc_modules = doc_modules
        self.checkpoints = Container()
        self.actions = Container()
        self.hybrids = Container()
        self.grafts = Container()
        # TODO: not use dict
        self.doc_funcs = {}
        self.env = Env()
        self.params = Params()
        self.extra_handler = None
        # TODO: support more case generator
        self.case_gen = DependGraphCaseGenerator()

        def _handle_func(func, conatiner):
            if is_TestObject(func):
                inst_func = func()
                conatiner.add(inst_func)
            else:
                conatiner.add(func)

        for module in modules:
            # TODO: remove dup code
            for _, func in inspect.getmembers(module, is_Action):
                _handle_func(func, self.actions)
            for _, func in inspect.getmembers(module, is_CheckPoint):
                _handle_func(func, self.checkpoints)
            for _, func in inspect.getmembers(module, is_Hybrid):
                _handle_func(func, self.hybrids)

            for _, func in inspect.getmembers(module, is_Graft):
                self.grafts |= Container(get_all_depend(func, depend_cls=Graft))

        for func in self.all_funcs:
            for module in self.doc_modules:
                name = get_name(func)
                if getattr(module, name, None):
                    self.doc_funcs[name] = getattr(module, name)
                    break

    def run(self):
        raise NotImplementedError

    def prepare(self):
        raise NotImplementedError

    @property
    def all_funcs(self):
        return self.hybrids | self.checkpoints | self.actions

    def _cb_filter_with_param(self, func):
        param_req = get_func_params_require(func)
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
        need_remove = Container()
        for func in container:
            if cb(func):
                LOGGER.debug('Remove func ' + str(func))
                need_remove.add(func)
        container -= need_remove

    def get_all_depend_provider(self, depend):
        providers = []
        if depend.type == Consumer.REQUIRE:
            req_types = [Provider.SET]
        elif depend.type == Consumer.REQUIRE_N:
            req_types = [Provider.CLEAR]
        else:
            return providers
        for func in self.actions:
            tmp_depends = get_all_depend(func, req_types, ret_list=False)
            if depend.env_depend in tmp_depends.keys():
                providers.append(func)
        return providers

    def find_checkpoints(self):
        ret = []
        for func in self.checkpoints:
            requires = get_all_depend(func, depend_cls=Consumer)
            if self.env.hit_requires(requires):
                ret.append(func)
        return ret

    def full_logger(self, msg):
        # TODO
        LOGGER.info(msg)
        self.params.doc_logger.info(msg)

    def run_one_step(self, func, step_index=None, check=False, only_doc=True):
        if getattr(func, '__name__', None):
            doc_func_name = func.__name__
        else:
            doc_func_name = func.__class__.__name__

        if step_index is not None:
            # TODO: move doc_logger definition in basic engine
            if only_doc:
                self.params.doc_logger.info('%d.\n' % step_index)
            else:
                self.full_logger('%d.\n' % step_index)
            step_index += 1

        if doc_func_name not in self.doc_funcs.keys():
            raise Exception("Not define %s name in doc modules" % doc_func_name)
        doc_func = self.doc_funcs[doc_func_name]

        LOGGER.debug("Start transfer env, func: %s env: %s", func, self.env)
        new_env = self.env.gen_transfer_env(func)
        if new_env is None:
            raise Exception("Fail to gen transfer env")

        LOGGER.debug("Env transfer to %s", new_env)

        # XXX: we transfer the env before the test func, and test func can update info in the env
        if self.extra_handler:
            self.extra_handler.handle_func(func, doc_func, new_env, True)
        else:
            if only_doc:
                doc_func(self.params, new_env)
            else:
                func(self.params, new_env)
        self.env = new_env

        if not check:
            return step_index

        checkpoints = self.find_checkpoints()
        for i, checkpoint in enumerate(checkpoints):
            if checkpoint == func:
                continue
            step_index = self.run_one_step(checkpoint,
                step_index=step_index, only_doc=only_doc)

        return step_index

    def _run_case_internal(self, case, case_id, test_func, need_cleanup, only_doc):
        step_index = 1
        extra_cases = {}
        self.full_logger("=" * 8 + " case %d " % case_id + "=" * 8)
        have_extra_cases = False
        try:
            steps = list(case.steps)
            LOGGER.debug("Case steps: %s", steps)
            if test_func:
                test_func = test_func
            else:
                test_func = steps[-1]
                steps = steps[:-1]

            for func in steps:
                # TODO support real case
                step_index = self.run_one_step(func,
                    step_index=step_index, only_doc=only_doc)

            with self.extra_handler.watch_func():
                step_index = self.run_one_step(test_func,
                        step_index=step_index, check=self.params.extra_check, only_doc=only_doc)

            tmp_cases = self.extra_handler.gen_extra_cases(case, self.env, test_func)
            if tmp_cases:
                have_extra_cases = True
                for cases_name, case in tmp_cases:
                    extra_cases.setdefault(cases_name, []).append(case)
        # TODO: move MistDeadEndException to handler
        except MistDeadEndException:
            # TODO: maybe need clean up
            pass
        else:
            if need_cleanup:
                if not case.cleanups:
                    LOGGER.info("Cannot find clean up way")
                    LOGGER.info("Current Env: %s", self.env)
                else:
                    for func in case.clean_ups:
                        step_index = self.run_one_step(func, step_index=step_index, only_doc=only_doc)
        # TODO: remove this
        self.env = Env()
        return extra_cases, have_extra_cases

    def run_case(self, case, case_id, test_func=None, need_cleanup=None, only_doc=True):
        if self.extra_handler:
            with self.extra_handler.start_handle():
                return self._run_case_internal(case, case_id, test_func, need_cleanup, only_doc)
        else:
            return self._run_case_internal(case, case_id, test_func, need_cleanup, only_doc)


class Template(BaseEngine):
    pass


class StaticTemplate(Template):
    pass


class MatrixTemplate(Template):
    pass


class Fuzz(BaseEngine):
    pass


class AI(BaseEngine):
    pass


class Demo(BaseEngine):
    """
    Standerd Engine + Mist handler
    """
    def __init__(self, basic_modules, test_modules=None,
                 test_funcs=None, doc_modules=None):
        self.test_modules = test_modules
        self.test_funcs = test_funcs
        tmp_modules = []
        tmp_modules.extend(basic_modules)
        if self.test_modules:
            tmp_modules.extend(test_modules)
        super(Demo, self).__init__(tmp_modules, doc_modules)
        self.extra_handler = MistsHandler(self)

    def _prepare_test_funcs(self):
        if not self.test_modules and not self.test_funcs:
            raise Exception('Need give a test object !')
        if self.test_modules:
            test_funcs = []
            for module in self.test_modules:
                for _, func in inspect.getmembers(module, (is_Action, is_Hybrid)):
                    test_funcs.append(func)
        else:
            test_funcs = self.test_funcs
        return test_funcs

    def _start_test(self, test_func, need_cleanup=False,
                    full_matrix=True, max_cases=None, only_doc=True):
        if getattr(test_func, 'func_name', None):
            title = getattr(test_func, 'func_name')
        else:
            title = str(test_func)

        self.params.doc_logger = self.params.case_logger
        self.full_logger("=" * 8 + " %s " % title + "=" * 8)
        self.full_logger("")
        target_env = Env.gen_require_env(test_func)
        i = 1
        with time_log('Compute case permutations'):
            case_matrix = sorted(list(self.case_gen.gen_cases(self.env, target_env, need_cleanup=need_cleanup)))

        LOGGER.info('Find %d valid cases', len(case_matrix))

        runner = Runner(self.params, self.env, self.checkpoints, self.doc_funcs, self.params.logger, self.params.doc_logger, self.extra_handler)
        # TODO use a class to be a cases container
        extra_cases = {}
        while case_matrix:
            case = case_matrix.pop(0)
            #new_extra_cases, is_mist = self.run_case(case, i, test_func, need_cleanup, only_doc=only_doc)
            new_extra_cases, is_mist = runner.run_case(case, i, test_func, need_cleanup, only_doc=only_doc)
            if not full_matrix and not is_mist:
                break
            for mist_name, cases in new_extra_cases.items():
                extra_cases.setdefault(mist_name, []).extend(cases)
            i += 1
            if max_cases and i > max_cases:
                break

        LOGGER.info("find another %d extra cases", len(extra_cases))
        self.params.doc_logger = self.params.mist_logger
        runner = Runner(self.params, self.env, self.checkpoints, self.doc_funcs, self.params.logger, self.params.doc_logger, self.extra_handler)
        for name, extra_case in extra_cases.items():
            for case in sorted(extra_case):
                #ret, is_mist = self.run_case(case, i, need_cleanup=need_cleanup, only_doc=only_doc)
                ret, is_mist = runner.run_case(case, i, need_cleanup=need_cleanup, only_doc=only_doc)
                if is_mist:
                    raise NotImplementedError
                i += 1
                if not full_matrix:
                    break

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

    def prepare(self):
        self.filter_all_func_custom(self._cb_filter_with_param)
        with time_log('Gen the depend map'):
            self.case_gen.gen_depend_map(Env(), self.actions | self.hybrids, self.params.drop_env)

    def run(self, params, doc_file=None):
        self.params = params
        LOGGER.debug(self.params.pretty_display())
        # TODO
        with self.preprare_logger(doc_file):
            self.prepare()

            tests = []
            test_funcs = self._prepare_test_funcs()

            while test_funcs:
                # TODO
                self.env = Env()
                test_case = []
                order = []
                test_func = random.choice(test_funcs)
                test_funcs.remove(test_func)
                self._start_test(test_func,
                                 full_matrix=self.params.full_matrix,
                                 max_cases=self.params.max_cases,
                                 only_doc=True if self.params.test_case else False)
