import functools
from typing import Callable, Any

def sanitize_kra_url(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Middleware decorator that programmatically strips whitespace from all string arguments.
    This is a mandatory fix for KRA GavaConnect where extra whitespace in URL segments
    leads to invalid signature or routing errors.
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Strip all string positional arguments
        new_args = list(args)
        for i, arg in enumerate(new_args):
            if isinstance(arg, str):
                new_args[i] = arg.strip()
        
        # Strip all string keyword arguments
        for key, value in kwargs.items():
            if isinstance(value, str):
                kwargs[key] = value.strip()
                
        return func(*tuple(new_args), **kwargs)
    return wrapper
