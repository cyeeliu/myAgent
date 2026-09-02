"""Sample module for evaluation fixtures."""


def foo():
    """Returns hello."""
    return "hello"


def bar():
    """Returns world."""
    return "world"


def baz(x):
    """Returns x * 2."""
    return x * 2


if __name__ == "__main__":
    print(foo())
    print(bar())
