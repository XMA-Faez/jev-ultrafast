"""uv run python scripts/inspect_page.py URL [--screenshot out.jpg] [--wait SECONDS]

Print the element table, controls and visible text Jev would see on URL, in a private headless Chrome. No model calls.
"""

import argparse
import base64
import time

from jev_ultrafast.browser import Browser
from jev_ultrafast.launch import launch_chrome
from jev_ultrafast.model import action_space


def print_observation(page):
    elements, _, controls = action_space(page["actions"])
    print(f"{page['url']}\n{page['title']}\nscroll {page['scroll']}  omitted {page['omitted_actions']}  "
          f"skipped frames {page.get('skipped_frames', 0)}\n")
    for element in elements:
        details = {k: element[k] for k in ("value", "checked", "selected", "expanded") if element.get(k)}
        options = [option["label"].split(" → ")[-1] for option in element.get("options", [])]
        print(f"[{element['index']:>3}] {element['role']:<10} {'/'.join(element['operations']):<16} "
              f"{element['label']!r} {details or ''} {options or ''}")
    print("\ncontrols:", ", ".join(controls))
    print("\n--- visible text ---\n" + page["text"][:3000])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--screenshot")
    parser.add_argument("--wait", type=float, default=0)
    arguments = parser.parse_args()
    chrome = launch_chrome()
    browser = None
    try:
        browser = Browser(arguments.url)
        time.sleep(arguments.wait)
        page = browser.observe(screenshot=bool(arguments.screenshot))
        print_observation(page)
        if arguments.screenshot:
            with open(arguments.screenshot, "wb") as image:
                image.write(base64.b64decode(page["screenshot"]))
    finally:
        if browser:
            browser.close()
        chrome.close()


if __name__ == "__main__":
    main()
