import functools
from typing import Callable, Any

def sanitize_kra_url(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Middleware decorator that programmatically strips trailing spaces from all URL strings.
    This is a mandatory fix for KRA GavaConnect production silent failures where 
    extra whitespace in the URL leads to invalid signature errors.
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Check positional arguments for strings that look like URLs
        new_args = list(args)
        for i, arg in enumerate(new_args):
            if isinstance(arg, str) and (arg.startswith("http://") or arg.startswith("https://")):
                new_args[i] = arg.strip()
        
        # Check keyword arguments
        for key, value in kwargs.items():
            if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://")):
                kwargs[key] = value.strip()
                
        return func(*tuple(new_args), **kwargs)
    return wrapper
