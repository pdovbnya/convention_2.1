# -*- coding: utf8 -*-

# ---------------------------------------------------------------------------------------------------------------------------------------- #
# ----- ПРИМЕР ЗАПУСКА РАСЧЕТА ЧЕРЕЗ API (МЕТОД CALCULATE) ------------------------------------------------------------------------------- #
# ---------------------------------------------------------------------------------------------------------------------------------------- #

import numpy as np
import time
import json
from requests import post

link = u'https://калькулятор.дом.рф:8193/Convention2/v2/Calculate'

params = {
            'bondID': 'RU000A1074A5',
            'zSpread': 100,
         }

header_dict = {'Content-Type': 'application/json'}
result = post(link, json = params, headers = header_dict).json()
