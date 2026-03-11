import asyncio
import functools
from typing import Callable, Any


def sanitize_kra_url(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Async-aware middleware decorator that strips whitespace from all string
    arguments before dispatch.

    KRA GavaConnect silently fails on URL segments with leading/trailing
    spaces — this decorator is the surgical fix applied at the boundary.

    Branches on asyncio.iscoroutinefunction so it returns the correct
    wrapper type, preventing event-loop deadlocks in async frameworks
    (FastAPI, Starlette, etc.).
    """
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            new_args = tuple(
                arg.strip() if isinstance(arg, str) else arg for arg in args
            )
            new_kwargs = {
                k: v.strip() if isinstance(v, str) else v
                for k, v in kwargs.items()
            }
            return await func(*new_args, **new_kwargs)
        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        new_args = tuple(
            arg.strip() if isinstance(arg, str) else arg for arg in args
        )
        new_kwargs = {
            k: v.strip() if isinstance(v, str) else v
            for k, v in kwargs.items()
        }
        return func(*new_args, **new_kwargs)

    return sync_wrapper
