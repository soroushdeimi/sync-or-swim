def pytest_configure(config):
    config.addinivalue_line("markers", "slow: restarts containers; deselect with -m 'not slow'")
