"""Colons CLI package"""
def main(argv=None):
    from .main import main as run
    return run(argv)

__all__ = ["main"]
