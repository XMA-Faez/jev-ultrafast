def pytest_addoption(parser):
    parser.addoption(
        "--real-browser",
        action="store_true",
        help="Attach to the user's running Chrome through Browser Harness instead of launching headless Chrome.",
    )
