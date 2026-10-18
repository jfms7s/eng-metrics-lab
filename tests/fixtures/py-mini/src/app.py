import json

import requests


def fetch(url):
    return requests.get(url, timeout=5)


def unused_helper(value):
    return 1
