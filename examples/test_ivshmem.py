from utils import enter_depend_test, STEPS, RESULT, SETUP
enter_depend_test()

from depend_test_framework.test_object import Action, CheckPoint, TestObject, Mist, MistDeadEndException, MistClearException
from depend_test_framework.dependency import Provider, Consumer
from depend_test_framework.base_class import ParamsRequire


@Action.decorator(1)
@ParamsRequire.decorator(['guest_name', 'ivshmem'])
@Consumer.decorator('$guest_name.config', Consumer.REQUIRE)
@Provider.decorator('$guest_name.config.ivshmem', Provider.SET)
def set_ivshmem_device(params, env):
    """
    """
    pass

@CheckPoint.decorator(2)
@ParamsRequire.decorator(['guest_name', 'ivshmem'])
@Consumer.decorator('$guest_name.active', Consumer.REQUIRE)
@Consumer.decorator('$guest_name.active.ivshmem', Consumer.REQUIRE)
def check_ivshmem_in_guest(params, env):
    pass

@CheckPoint.decorator(1)
@ParamsRequire.decorator(['guest_name', 'ivshmem'])
@Consumer.decorator('$guest_name.active', Consumer.REQUIRE)
@Consumer.decorator('$guest_name.active.ivshmem', Consumer.REQUIRE)
def check_ivshmem_cmdline(params, env):
    pass

@CheckPoint.decorator(1)
@ParamsRequire.decorator(['guest_name', 'ivshmem'])
@Consumer.decorator('$guest_name.config', Consumer.REQUIRE)
def check_ivshmem_audit(params, env):
    pass

@Action.decorator(1)
@ParamsRequire.decorator(['guest_name', 'ivshmem'])
@Consumer.decorator('$guest_name.active', Consumer.REQUIRE)
@Provider.decorator('$guest_name.active.ivshmem', Provider.SET)
def hot_plug_ivshmem(params, env):
    pass

@Action.decorator(1)
@ParamsRequire.decorator(['guest_name', 'ivshmem'])
@Consumer.decorator('$guest_name.active', Consumer.REQUIRE)
@Consumer.decorator('$guest_name.active.ivshmem', Consumer.REQUIRE)
@Provider.decorator('$guest_name.active.ivshmem', Provider.CLEAR)
def hot_unplug_ivshmem(params, env):
    pass
