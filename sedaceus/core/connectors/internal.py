from __future__ import annotations

from types import MethodType
from typing import Callable
import asyncio


__all__ = (
    "DispatchFramework",
    "listen",
    "ListenerClass",
    "WaitForCheck",
)


# T = TypeVar("T")


# _logger = logging.getLogger(__name__)


# class ClassMaker:
#     """
#     Used to delay creating an object from a given class with the given args, kwargs, and optionally the
#     parent class object.
#
#     Can only be used inside a class subclassing :class:`ClassMakerMixin`
#
#     Parameters
#     ----------
#     cls: type
#         Class to instantiate and return when called.
#     args: tuple[Any, ...]
#         Positional arguments to instantiate the class with.
#     kwargs: dict[str, Any]
#         Keyword arguments to instantiate the class with.
#     """
#     name: str | None
#     cls: type
#     args: tuple[Any, ...]
#     kwargs: dict[str, Any]
#
#     def __init__(self, cls: type, *args, **kwargs):
#         self.cls = cls
#         self.args = args
#         self.kwargs = kwargs
#
#     def __set_name__(self, owner: type[ClassMakerMixin], name: str):
#         if issubclass(owner, ClassMakerMixin):
#             self.name = name
#             owner._added_makers[name] = self
#         else:
#             raise ValueError("A class utilizing ClassMaker must subclass ClassMakerMixin")
#
#     def __call__(self, obj: ClassMakerMixin | None = None):
#         if obj:
#             return self.cls(obj, *self.args, **self.kwargs)
#         else:
#             return self.cls(*self.args, **self.kwargs)


# class ClassMakerMixin:
#     """
#     Subclassed to automatically have added :class:`ClassMaker` objects assigned in the class to fully instantiate
#     themselves as per-instance objects.
#     """
#     _added_makers: dict[str, ClassMaker] = {}
#
#     def __new__(cls):
#         ret = super(ClassMakerMixin, cls).__new__(cls)
#
#         for name, maker in cls._added_makers.items():
#             ret.__dict__[name] = maker(ret)
#
#         return ret


# class IterQueue(queue.Queue, Generic[T]):
#     def __iter__(self):
#         return self
#
#     def __next__(self) -> T:
#         try:
#             return self.get_nowait()
#         except queue.Empty:
#             raise StopIteration


# @ClassMaker
class ListenerClass:
    event: str | type
    callback: Callable

    def __init__(self, event: str | type, callback: Callable):
        self.event = event
        self.callback = callback


# class ListenerClass2:
#     def __init__(self, listen_for: str | type, callback: Callable):
#         self.listen_for = listen_for
#         self.callback: Callable = callback
#         self.self_arg = None
#
#     async def __call__(self, *args, **kwargs):
#         if self.self_arg:
#             await self.callback(self.self_arg, *args, **kwargs)
#         else:
#             await self.callback(*args, **kwargs)


class WaitForCheck:
    def __init__(self, future: asyncio.Future, check: Callable[..., bool]):
        self.future: asyncio.Future = future
        self.check: Callable[..., bool] = check


class DispatchFramework:
    __permanent_listeners__: dict[str | type, set[Callable]]
    __temporary_listeners__: dict[str | type, set[WaitForCheck]]

    def __new__(cls, *args, **kwargs):
        new_cls = super(DispatchFramework, cls).__new__(cls)
        new_cls._discover_listeners()
        return new_cls

    def _discover_listeners(self):
        self.__permanent_listeners__ = {}
        self.__temporary_listeners__ = {}
        for base in reversed(self.__class__.__mro__):
            for elem, value in base.__dict__.items():
                if isinstance(value, staticmethod):
                    value = value.__func__

                if isinstance(value, ListenerClass):
                    if not self.__permanent_listeners__.get(value.event):
                        self.__permanent_listeners__[value.event] = set()

                    base.__dict__[elem] = MethodType(value.event, self)
                    self.__permanent_listeners__[value.event].add(base.__dict__[elem])

    def add_listener(self, func: ListenerClass | Callable, event: str | type | None = None):
        if isinstance(func, ListenerClass):
            event = event or func.event
            func = func.callback

        if event is None:
            raise ValueError("A listener can't listen for everything, listen_for must be provided.")

        if event not in self.__permanent_listeners__:
            self.__permanent_listeners__[event] = set()

        self.__permanent_listeners__[event].add(func)

    def listen(self, event: str | type | None = None):
        def wrapper(func: Callable):
            name = event or func.__name__
            self.add_listener(func=func, event=name)
            return func

        return wrapper

    def wait_for(self, event: type | str, check: Callable[..., bool] | None = None, timeout: float | None = None):
        future = asyncio.get_running_loop().create_future()
        if not check:
            def _check(*args, **kwargs):
                return True

            check = _check

        if event not in self.__temporary_listeners__:
            self.__temporary_listeners__[event] = set()

        self.__temporary_listeners__[event].add(WaitForCheck(future, check))
        return asyncio.wait_for(future, timeout=timeout)

    def dispatch(self, event: type | str, *args, **kwargs):
        loop = asyncio.get_running_loop()
        if event in self.__temporary_listeners__:
            temp_listeners = self.__temporary_listeners__[event].copy()
            for listener in temp_listeners:
                if listener.future.cancelled():
                    self.__temporary_listeners__[event].remove(listener)
                else:
                    try:
                        result = listener.check(*args, **kwargs)
                    except Exception as e:
                        listener.future.set_exception(e)
                    else:
                        if result:
                            match len(args):
                                case 0:
                                    listener.future.set_result(None)
                                case 1:
                                    listener.future.set_result(args[0])
                                case _:
                                    listener.future.set_result(args)

                            self.__temporary_listeners__[event].remove(listener)

        for listener in self.__permanent_listeners__.get(event, set()):
            loop.create_task(listener(*args, **kwargs))


# class DispatchFramework2:
#     # __added_listeners__: Dict[Union[str, type], Set[ListenerClass]]
#     __permanent_listeners__: dict[str | type, set[ListenerClass2]]
#     # __temp_listeners__: Dict[Union[str, type], Set[WaitForCheck]]
#     __temporary_listeners__: dict[str | type, set[WaitForCheck]]
#
#     def __new__(cls, *args, **kwargs):
#         new_cls = super(DispatchFramework2, cls).__new__(cls)
#         new_cls._discover_listeners()
#         return new_cls
#
#     def _discover_listeners(self):
#         self.__permanent_listeners__ = {}
#         self.__temporary_listeners__ = {}
#         for base in reversed(self.__class__.__mro__):
#             for elem, value in base.__dict__.items():
#                 is_static_method = isinstance(value, staticmethod)
#                 if is_static_method:
#                     value = value.__func__
#                 if isinstance(value, ListenerClass2):
#                     if not self.__permanent_listeners__.get(value.listen_for):
#                         self.__permanent_listeners__[value.listen_for] = set()
#                     value.self_arg = self
#                     self.__permanent_listeners__[value.listen_for].add(value)
#
#     def add_listener(
#             self,
#             listener: ListenerClass2 | Callable,
#             listen_for: str | None = None
#     ):
#         if inspect.ismethod(listener):
#             if listen_for is None:
#                 raise ValueError("When given listener is a method, listen_for must be provided.")
#             listener = ListenerClass2(listen_for=listen_for, callback=listener)
#         elif isinstance(listener, ListenerClass2):
#             pass
#
#         if not self.__added_listeners__.get(listener.listen_for):
#             self.__added_listeners__[listener.listen_for] = set()
#
#         self.__added_listeners__[listener.listen_for].add(listener)
#
#     def dispatch(self, to_dispatch: Union[str, type], *args, **kwargs):
#         loop = asyncio.get_running_loop()
#         if to_dispatch in self.__temp_listeners__:
#             listeners = self.__temp_listeners__[to_dispatch].copy()
#             for listener in listeners:
#                 if listener.future.cancelled():
#                     self.__temp_listeners__[to_dispatch].remove(listener)
#                 else:
#                     try:
#                         result = listener.check(*args, **kwargs)
#                     except Exception as e:
#                         listener.future.set_exception(e)
#                     else:
#                         if result:
#                             if len(args) == 0:
#                                 listener.future.set_result(None)
#                             elif len(args) == 1:
#                                 listener.future.set_result(args[0])
#                             else:
#                                 listener.future.set_result(args)
#
#                             self.__temp_listeners__[to_dispatch].remove(listener)
#
#         for listener in self.__added_listeners__.get(to_dispatch, set()):
#             loop.create_task(listener(*args, **kwargs))
#
#     def wait_for(
#             self,
#             event: type | str,
#             check: Callable[..., bool] | None = None,
#             timeout: float | None = None
#     ) -> Any:
#         future = asyncio.get_running_loop().create_future()
#         if not check:
#             def _check(*args, **kwargs):
#                 return True
#             check = _check
#         if event not in self.__temp_listeners__:
#             self.__temp_listeners__[event] = set()
#
#         self.__temp_listeners__[event].add(WaitForCheck(future, check))
#         return asyncio.wait_for(future, timeout)


def listen(listen_for: str | type):
    def wrapper(func: Callable):
        if asyncio.iscoroutinefunction(func):
            ret = ListenerClass(listen_for, func)
            ret.__call__ = func
            return ret
        else:
            raise ValueError("Given function is not a coroutine.")
    return wrapper

