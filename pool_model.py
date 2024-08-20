# -*- coding: utf8 -*-

# ---------------------------------------------------------------------------------------------------------------------------------------- #
# ----- КОНВЕНЦИЯ ДЛЯ ИПОТЕЧНЫХ ЦЕННЫХ БУМАГ: МОДЕЛЬ ДЕНЕЖНОГО ПОТОКА ПО ИПОТЕЧНОМУ ПОКРЫТИЮ --------------------------------------------- #
# ---------------------------------------------------------------------------------------------------------------------------------------- #

import pandas as pd
import numpy as np
import time
import copy

from cachetools import cached, LRUCache
from requests import get
from iteround import saferound

from convention_2.auxiliary import *
from convention_2.macro_model import *

import warnings

warnings.filterwarnings('ignore')
np.seterr(all='ignore')


def loansCashflowModel(bond_id, report_date, key_rate_model_date, key_rate_model_data, s_curves, cdr,
                       ifrs=False, reinvestment=False, stop_date=None, key_rate_forecast=None,
                       progress_bar=None, connection_id=None, current_percent=0.0, status_delta=0.0,
                       pool_data_path=None):
    """
    ----------------------------------------------------------------------------------------------------------------------------------------
    Моделирование помесячных погашений основного долга, процентных поступлений и субсидий по ипотечному покрытию
    ----------------------------------------------------------------------------------------------------------------------------------------

    Параметры функции:

        Обязательные:
            1. bond_id               — ISIN или регистрационный номер выпуска ИЦБ ДОМ.РФ
            2. report_date           — дата среза ипотечного покрытия
            3. key_rate_model_date   — дата, по состоянию на которую производится расчет Модельной траектории среднемесячной рыночной
                                       ставки рефинансирования ипотеки (Опорная дата модели Ключевой ставки)
            4. key_rate_model_data   — данные, необходимые для расчета необходимые для расчета Модельной траектории Ключевой ставки и
                                       Модельной траектории среднемесячной ставки рефинансирования ипотеки
            5. s_curves              — Параметры S-кривых для расчета
            6. cdr                   — значение Модельного CDR

        Опциональные:
            1. ifrs                 — True/False: моделировать ипотечное покрытие с учетом требований МСФО, по умолчанию false
            2. reinvestment         — True/False: добавить в качестве выходных данных модели ежедневный денежный поток на баланс Ипотечного
                                      агента для дальнейшего расчета начисления процентной ставки на остаток на счете Ипотечного агента
            3. stop_date            — точный день в формате даты, до которого необходимо моделировать платежи по кредитам
            4. key_rate_forecast    — пользовательская траектория значений Ключевой ставки
            5. Для отправки процентов готовности расчета:
                    5.1 progress_bar         — запущенный в консоли progress bar
                    5.2 connection_id        — идентификатор соединения с сайтом
                    5.3 current_percent      — текущее значение готовности расчета в процентах
                    5.4 status_delta         — дельта в процентах, на которую нужно увеличивать значение готовности расчета
            6. pool_data_path       — путь на csv файл с данными по кредитам в ипотечном покрытии (запуск модели в обход основного расчета)

    ----------------------------------------------------------------------------------------------------------------------------------------

    Алгоритм расчета:

    1.	Для расчета денежного потока по кредиту в ипотечном покрытии необходимы следующие параметры (подробнее о каждом параметре см.
        описание метода GetPoolsData в методике):
            — Дата выдачи кредита
            — Текущая дата погашения кредита
            — Текущий остаток основного долга по кредиту
            — Текущая процентная ставка по кредиту
            — Тип платежа по кредиту
            — День начала процентного периода по кредиту
            — Гос. программа по кредиту [при наличии]
            — Вычет для расчета субсидии по кредиту [при наличии]
        Параметры кредитов выгружаются методом GetPoolsData на Дату среза ипотечного покрытия для расчета

    2.  По состоянию на Опорную дату модели Ключевой ставки рассчитываются Модельная траектория Ключевой ставки и Модельная траектория
        среднемесячной рыночной ставки рефинансирования ипотеки

    3.	Согласно текущей дате погашения и дню начала процентного периода для каждого кредита j рассчитывается последовательность дат,
        в которые по кредиту ожидаются выплаты ежемесячных платежей

    4. 	Для каждой (!) даты платежа i каждого (!) кредита j рассчитываются:

            4.1. Размер погашения остатка основного долга по графику платежей [в терминах доли от остатка основного долга на начало
                 процентного периода, заканчивающегося датой платежа i]
            4.2. Количество полных лет, которое прошло с даты выдачи кредита j до начала процентного периода, заканчивающегося датой
                 платежа i (выдержка кредита)
            4.3. Стимул к рефинансированию как разница между текущей процентной ставки по кредиту j и среднемесячной рыночной ставкой
                 рефинансирования ипотеки за месяц, предшествующий месяцу, на который приходится дата платежа i [в процентных пунктах]
            4.4. Ожидаемый темп частичных и полных досрочных погашений CPR как значение S-кривой соответствующей выдержки в точке стимула
                 к рефинансированию (подробнее см. раздел про S-кривые в методике) [% годовых]
            4.5. Размер досрочного погашения остатка основного долга в части частичных/полных досрочных погашений на основании рассчитанного
                 значения CPR [в терминах доли от остатка основного долга на начало процентного периода, заканчивающегося датой платежа i]
            4.6. Размер досрочного погашения остатка основного долга в части выкупа дефолтов на основании Модельного CDR [в терминах доли
                 от остатка основного долга на начало процентного периода, заканчивающегося датой платежа i]
            4.7. На основании п. 4.1, 4.5, 4.6 – размер погашения основного долга [рубли]
            4.8. Размер процентных поступлений (с учетом недополучения процентов при полном досрочном погашении и выкупе дефолта) [рубли]

    5. Объединение (суммирование) денежных потоков по кредитам в совокупный помесячный денежный поток по ипотечному покрытию, а также
       отдельно в помесячный денежный поток по ипотечному покрытию в части кредитов без субсидий и отдельно в помесячный денежный поток
       по ипотечному покрытию в части кредитов с субсидиями

    ----------------------------------------------------------------------------------------------------------------------------------------

    Результат функции:

            1. poolStatistics   — статистика ипотечного покрытия (подробнее см. описание объекта stats далее)
            2. macroModel       — результат модели макроэкономики (подробнее см. описание в macro_model.py)
            3. poolModel        — результат модели денежного потока по ипотечному покрытию (подробнее см. описание объекта poolModel далее)

    ----------------------------------------------------------------------------------------------------------------------------------------
    """

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- СКАЧИВАНИЕ ДАННЫХ ПО КРЕДИТАМ В ИПОТЕЧНОМ ПОКРЫТИИ --------------------------------------------------------------------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    # Данные по кредитам в ипотечном покрытии (poolData):
    #       · issueDate            — Дата выдачи кредита
    #       · currentMaturityDate  — Текущая дата погашения кредита
    #       · currentDebt          — Текущий остаток основного долга по кредиту  [РУБЛИ]
    #       · currentRate          — Текущая процентная ставка по кредиту        [% ГОДОВЫХ]
    #       · paymentType          — Тип платежа по кредиту                      [0 - АННУИТЕТНЫЙ, 1 - ДИФФ.]
    #       · startInterestDay     — День начала процентного периода             [ОТ 1 ДО 31]
    #       · governProgramType    — Гос. программа по кредиту                   [NONE/1/2/3/4/5]
    #       · keyRateDeduction     — Вычет для расчета субсидии по кредиту       [П.П]

    poolData = None
    if pool_data_path is None:
        server_output = get(API.GET_POOL_DATA.format(bond_id, report_date, ifrs), timeout=30).json()
        reportDate = np.datetime64(server_output['pools'][0]['reportDate'], 'D')
        if report_date != reportDate:
            warnings.warn(WARNINGS._1.format(bond_id, report_date, reportDate))
        poolData = server_output['pools'][0]['data']
    else:
        reportDate = report_date
        poolData = pd.read_excel(pool_data_path).to_dict('list')

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- ОБРАБОТКА ДАННЫХ ПО КРЕДИТАМ В ИПОТЕЧНОМ ПОКРЫТИИ И РАСЧЕТ СТАТИСТИКИ -------------------------------------------------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    issue_dates = np.array(poolData['issueDate']).astype(d_type)  # ДАТА ВЫДАЧИ КРЕДИТА
    maturity_dates = np.array(poolData['currentMaturityDate']).astype(d_type)  # ТЕКУЩАЯ ДАТА ПОГАШЕНИЯ КРЕДИТА
    current_debts = np.array(poolData['currentDebt']).astype(float)  # ТЕКУЩИЙ ОСТАТОК ОСНОВНОГО ДОЛГА ПО КРЕДИТУ (РУБЛИ)
    current_rates = np.array(poolData['currentRate']).astype(float)  # ТЕКУЩАЯ ПРОЦЕНТНАЯ СТАВКА ПО КРЕДИТУ (% ГОДОВЫХ)
    payment_types = np.array(poolData['paymentType']).astype(int)  # ТИП ПЛАТЕЖА ПО КРЕДИТУ (0 - АННУИТЕТНЫЙ, 1 - ДИФФ.)
    start_days = np.array(poolData['startInterestDay']).astype(int)  # ДЕНЬ НАЧАЛА ПРОЦЕНТНОГО ПЕРИОДА ПО КРЕДИТУ (ОТ 1 ДО 31)
    govern_program_type = poolData['governProgramType']  # ГОС. ПРОГРАММА ПО КРЕДИТУ (NONE/1/2/3/4/5)
    key_rate_deduction = np.array(poolData['keyRateDeduction']).astype(float)  # ВЫЧЕТ ДЛЯ РАСЧЕТА СУБСИДИИ ПО КРЕДИТУ (П.П)

    # Количество кредитов в ипотечном покрытии:
    n = len(current_debts)

    # В том случае, если у поля paymentType все значения для всех кредитов одинаковые, метод GetPoolsData возвращает массив из одного
    # значения. Если это так, необходимо явно задать значение типа платежа для каждого кредита:
    if len(payment_types) < n and len(payment_types) == 1:
        payment_types = np.array([payment_types[0]] * n)

    # В том случае, если у поля startInterestDay все значения для всех кредитов одинаковые, метод GetPoolsData возвращает массив из одного
    # значения. Если это так, необходимо явно задать значение дня начала процентного периода для каждого кредита:
    if len(start_days) < n and len(start_days) == 1:
        start_days = np.array([start_days[0]] * n)

    # В том случае, если у поля governProgramType все значения для всех кредитов одинаковые, метод GetPoolsData возвращает массив из одного
    # значения. Если это так, необходимо явно задать тип гос. программы для каждого кредита:
    if len(govern_program_type) < n and len(govern_program_type) == 1:
        govern_program_type = np.array([govern_program_type[0]] * n)

    # В том случае, если у поля keyRateDeduction все значения для всех кредитов одинаковые, метод GetPoolsData возвращает массив из одного
    # значения. Если это так, необходимо явно задать тип гос. программы для каждого кредита:
    if len(key_rate_deduction) < n and len(key_rate_deduction) == 1:
        key_rate_deduction = np.array([key_rate_deduction[0]] * n)

    # Максимальная дата погашения кредита определяется как максимальная дата среди текущих дат погашений кредита по кредитам, у которых
    # остаток основного долга больше нуля:
    max_maturity_date = maturity_dates[current_debts > 0.0].max()

    # Иногда в ипотечных покрытиях появляются кредиты с нулевой ставкой. Сервисные агенты могут обозначать таким образом реструктуризацию.
    # Заменим ставку по таким кредитам с 0.0 на 0.001, чтобы корректно их обработать (код не предполагает работу с нулевыми ставками):
    current_rates[current_rates == 0.0] = 0.001

    # Точный день в формате даты, до которого необходимо моделировать платежи по кредитам. Если stop_date не указана, алгоритм моделирует
    # денежные потоки по всем кредитам до их текущей даты погашения, что может удлинить расчет для больших ипотечных покрытий:
    if stop_date is None:
        stop_date = maturity_dates.max()
    else:
        stop_date = min(stop_date, maturity_dates.max())

    # Код рассчитан на ситуацию, если report_date и stop_date приходятся на один и тот же месяц, при этом report_date не является началом
    # месяца, а stop_date не является его концом. В таком случае необходимо увеличить stop_date на месяц:
    initial_stop_date = None
    if stop_date.astype(m_type) == report_date.astype(m_type):
        days_in_month = ((report_date.astype(m_type) + month).astype(d_type) - report_date.astype(m_type).astype(d_type)) / day
        if (stop_date - report_date) / day + 1 < days_in_month:
            # При этом для некоторых задач необходимо сохранить первоначальное значение stop_date:
            initial_stop_date = copy.deepcopy(stop_date)
            stop_date = (stop_date.astype(m_type) + 2 * month).astype(d_type) - day

    # Тип ипотечного покрытия:
    fxd = np.array(govern_program_type) == None
    flt = np.array(govern_program_type) != None

    fxd_inside = fxd.any()
    flt_inside = flt.any()

    poolType = None
    if fxd_inside and not flt_inside:
        poolType = POOL_TYPE.FXD  # ТИП ИПОТЕЧНОГО ПОКРЫТИЯ 1: СТАНДАРТНЫЙ (ВСЕ КРЕДИТЫ БЕЗ СУБСИДИЙ)
    elif not fxd_inside and flt_inside:
        poolType = POOL_TYPE.FLT  # ТИП ИПОТЕЧНОГО ПОКРЫТИЯ 2: СУБСИДИРОВАННЫЙ (ВСЕ КРЕДИТЫ С СУБСИДИЯМИ)
    else:
        poolType = POOL_TYPE.MIX  # ТИП ИПОТЕЧНОГО ПОКРЫТИЯ 3: СМЕШАННЫЙ (ЧАСТЬ КРЕДИТОВ С СУБСИДИЯМИ, ЧАСТЬ БЕЗ СУБСИДИЙ)

    # Сумма остатков основного долга в ипотечном покрытии:
    debt = np.round(np.sum(current_debts), 2)

    # Сумма остатков основного долга в ипотечном покрытии по кредитам без субсидий (fxd_debt) и по кредитам с субсидиями (flt_debt):
    fxd_debt = np.round(np.sum(current_debts[fxd]), 2) if fxd_inside else None
    if fxd_debt is not None:
        flt_debt = np.round(debt - fxd_debt, 2) if flt_inside else None
    else:
        flt_debt = debt

    # Доля в ипотечном покрытии кредитов без субсидий (fxd_fraction) и кредитов с субсидиями (flt_fraction):
    fxd_fraction, flt_fraction = None, None
    if poolType is POOL_TYPE.FXD:
        fxd_fraction = 100.0
    elif poolType is POOL_TYPE.FLT:
        flt_fraction = 100.0
    elif poolType is POOL_TYPE.MIX:
        fxd_fraction = np.round(fxd_debt / debt * 100.0, 2)
        flt_fraction = np.round(100.0 - fxd_fraction, 2) if flt_inside else None

    # Статистика ипотечного покрытия. Значение каждой статистики (кроме reportDate и poolType) указывается отдельно для всего ипотечного
    # покрытия (total), для кредитов без субсидий (fixed) и для кредитов с субсидиями (float):
    stats = {
        'reportDate': str(reportDate.astype(s_type)),  # дата среза ипотечного покрытия
        'poolType': poolType,  # тип ипотечного покрытия
        'poolDebt': {'total': None, 'fixed': None, 'float': None},  # сумма остатков основного долга
        'poolFraction': {'total': None, 'fixed': None, 'float': None},  # доля в терминах остатков основного долга
        'loansNumber': {'total': None, 'fixed': None, 'float': None},  # количество кредитов
        'wac': {'total': None, 'fixed': None, 'float': None},  # средневзвешенная процентная ставка (WAC)
        'wala': {'total': None, 'fixed': None, 'float': None},  # средневзвешенная выдержка (WALA)
        'wam': {'total': None, 'fixed': None, 'float': None},  # средневзвешенный срок до погашения (WAM)
        'keyRateDeduction': {'total': None, 'fixed': None, 'float': None},  # средневзвешенный вычет для расчета субсидии (только для float)
        'keyRatePremium': {'total': None, 'fixed': None, 'float': None},  # средневзвешенная надбавка к Ключевой ставке (только для float)
    }

    stats['poolDebt']['total'] = debt
    stats['poolFraction']['total'] = None
    stats['loansNumber']['total'] = n
    stats['wac']['total'] = np.round(np.sum(current_rates * current_debts) / debt, 2)
    stats['wala']['total'] = np.round(np.sum((reportDate - issue_dates) / day / 365.0 * current_debts) / debt, 1)
    stats['wam']['total'] = np.round(np.sum((maturity_dates - reportDate) / day / 365.0 * current_debts) / debt, 1)
    stats['keyRateDeduction']['total'] = None
    stats['keyRatePremium']['total'] = None

    if fxd_inside:
        stats['poolDebt']['fixed'] = fxd_debt
        stats['poolFraction']['fixed'] = fxd_fraction
        stats['loansNumber']['fixed'] = int(fxd.sum())
        stats['wac']['fixed'] = np.round(np.sum(current_rates[fxd] * current_debts[fxd]) / fxd_debt, 2)
        stats['wala']['fixed'] = np.round(np.sum((reportDate - issue_dates[fxd]) / day / 365.0 * current_debts[fxd]) / fxd_debt, 1)
        stats['wam']['fixed'] = np.round(np.sum((maturity_dates[fxd] - reportDate) / day / 365.0 * current_debts[fxd]) / fxd_debt, 1)
        stats['keyRateDeduction']['fixed'] = None
        stats['keyRatePremium']['fixed'] = None

    if flt_inside:
        stats['poolDebt']['float'] = flt_debt
        stats['poolFraction']['float'] = flt_fraction
        stats['loansNumber']['float'] = int(flt.sum())
        stats['wac']['float'] = np.round(np.sum(current_rates[flt] * current_debts[flt]) / flt_debt, 2)
        stats['wala']['float'] = np.round(np.sum((reportDate - issue_dates[flt]) / day / 365.0 * current_debts[flt]) / flt_debt, 1)
        stats['wam']['float'] = np.round(np.sum((maturity_dates[flt] - reportDate) / day / 365.0 * current_debts[flt]) / flt_debt, 1)
        stats['keyRateDeduction']['float'] = np.round(np.sum(key_rate_deduction[flt] * current_debts[flt]) / flt_debt, 2)
        stats['keyRatePremium']['float'] = np.round(np.sum((current_rates[flt] + key_rate_deduction[flt]) * current_debts[flt]) / flt_debt,
                                                    2)

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- ЗАПУСК МОДЕЛИ КЛЮЧЕВОЙ СТАВКИ И СТАВКИ РЕФИНАНСИРОВАНИЯ ИПОТЕКИ -------------------------------------------------------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    # По состоянию на Опорную дату модели Ключевой ставки рассчитываются Модельная траектория Ключевой ставки и Модельная траектория
    # среднемесячной рыночной ставки рефинансирования ипотеки

    macroModel = refinancingRatesModel(key_rate_model_date=key_rate_model_date,
                                       key_rate_model_data=key_rate_model_data,
                                       start_month=reportDate.astype(m_type) - month,
                                       stop_month=stop_date.astype(m_type) + 10 * month,
                                       key_rate_forecast=key_rate_forecast)

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- ФОРМИРОВАНИЕ БУДУЩИХ ПРОЦЕНТНЫХ ПЕРИОДОВ ДЛЯ КАЖДОГО КРЕДИТА ----------------------------------------------------------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    # Последовательность месяцев с шагом в 1 месяц, начинающаяся с месяца, предшествующего месяцу даты среза, и заканчивающаяся месяцем,
    # следующим за месяцем максимальной текущей даты погашения кредита (горизонтальный вектор):
    all_months = np.arange(reportDate.astype(m_type) - month, maturity_dates.max().astype(m_type) + month * 2)

    # Количество дней в каждом месяце all_months (вертикальный вектор):
    days_in_months = ((((all_months + month).astype(d_type) - all_months.astype(d_type)) / day).astype(int)).reshape(-1, 1)

    # Формируем таблицу no_fit размером len(all_months) x n, значение ячейки (i,j) — индикатор True/False того, что в месяц i у кредита j
    # день начала процентного периода больше, чем количество дней в месяце:
    no_fit = start_days > days_in_months

    # Начинаем формировать таблицу start_dates размером len(all_months) x n, значение в ячейке (i,j) — это дата начала процентного
    # периода i у кредита j. В том случае, если в таблце no_fit стоит True, определяем начало процентного периода как начало следующего
    # месяца. Например, у кредита день начала процентного периода 31, в феврале 28 дней, тогда начало этого процентного периода у этого
    # кредита будет 1 марта:
    start_dates = all_months.astype(d_type).reshape(-1, 1) + np.where(no_fit, days_in_months, start_days) - 1
    start_dates[no_fit] += day

    # Начинаем формировать таблицу end_dates размером len(all_months) x n, значение в ячейке (i,j) — это дата конца процентного периода i
    # у кредита j. Дата конца процентного периода определяется как предыдущий день относительно дня начала следующего процентного периода:
    end_dates = start_dates - day

    # Сдвигаем таблицы таким образом, чтобы ячейка (i,j) в start_dates и ячейка (i,j) в end_dates соответствовали одному и тому же
    # процентному периоду по кредиту j:
    start_dates, end_dates = start_dates[:-1, :], end_dates[1:, :]

    # Проставляем пустыми те даты концов процентных периодов, которые выходят за пределы текущих дат погашения по кредитам, либо равны им:
    end_dates[end_dates >= maturity_dates] = d_nat

    # На предыдущем шаге можно было использовать строгое неравенство (>), однако в таком случае для кредитов, у которых день погашения
    # строго больше дня платежа, последний процентный период будет сокращен. Чтобы этого избежать, нужно было на предыдущем шаге устано-
    # вить нестрогое неравенство, а на следующем шаге:
    #       1. для кредитов, у которых день погашения строго больше дня платежа, на последнее непустое значение каждой колонки end_dates
    #          выставить текущую дату погашения;
    #       2. для кредитов, у которых день погашения меньше либо равен дню платежа, на первое пустое значение каждой колонки end_dates
    #          выставить текущую дату погашения:
    last_month_positions = np.count_nonzero(~np.isnat(end_dates), axis=0)
    double_last_month = end_dates[last_month_positions - 1, np.arange(0, n)].astype(m_type) == maturity_dates.astype(m_type)
    end_dates[last_month_positions - double_last_month, np.arange(0, n)] = maturity_dates

    # Текущий остаток основного долга по кредиту указывается для всех кредитов по состоянию на начало Даты среза ипотечного покрытия
    # (т.е. до платежей, которые могут прийти в дату среза ипотечного покрытия), поэтому проставляем пустыми те даты концов процентных
    # периодов, которые строго меньше даты среза ипотечного покрытия:
    end_dates[end_dates < reportDate] = d_nat

    # Проставляем равными датам выдач кредитов те даты начала процентных периодов, которые по алгоритму получились меньше, чем даты выдач:
    start_dates = np.where(start_dates < issue_dates, issue_dates, start_dates)

    # Проставляем пустыми даты начала тех процентных периодов, по которым не будет платежей:
    start_dates[np.isnat(end_dates)] = d_nat

    # Для удобства дальнейших расчетов все последовательности процентных периодов (т.е. колонки таблиц start_dates и end_dates) нужно
    # сдвинуть в нулевой индекс (т.е. чтобы процентный период следующей после даты среза выплаты приходился на индекс 0):
    nan_first_row = np.isnat(start_dates[0, :])
    while nan_first_row.any():
        nan_array = np.array([d_nat] * nan_first_row.sum())
        start_dates[:, nan_first_row] = np.vstack([start_dates[1:, nan_first_row], nan_array])
        end_dates[:, nan_first_row] = np.vstack([end_dates[1:, nan_first_row], nan_array])
        nan_first_row = np.isnat(start_dates[0, :])

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- РАСЧЕТ ПОМЕСЯЧНЫХ ПОГАШЕНИЙ ОСНОВНОГО ДОЛГА ПО ГРАФИКУ ПЛАТЕЖЕЙ ПО КАЖДОМУ КРЕДИТУ В КАЖДОМ ПРОЦЕНТНОМ ПЕРИОДЕ --------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    # Формируем таблицу periods_left размером len(start_dates) x n,
    # значение в ячейке (i,j) — количество месяцев до погашения кредита j в процентном периоде i:
    periods_left = np.count_nonzero(~np.isnat(start_dates), axis=0).astype(float)
    periods_left = periods_left - np.arange(0, len(start_dates), step=1.0).reshape(-1, 1)

    # Для кредитов, у которых день погашения строго больше дня платежа, добавляем один месяц,
    # потому что для них последний процентный период объединен в два:
    periods_left[:, double_last_month] += 1

    # Исключаем все процентные периоды, даты платежей которых выходят за пределы stop_date:
    not_needed = end_dates > stop_date
    start_dates[not_needed], end_dates[not_needed], periods_left[not_needed] = d_nat, d_nat, np.nan
    max_row = np.count_nonzero(~np.isnat(end_dates), axis=0).max() + 1
    start_dates, end_dates, periods_left = start_dates[:max_row, :], end_dates[:max_row, :], periods_left[:max_row, :]

    # Формируем таблицу periods_deltas размером len(start_dates) x n, значение в ячейке (i,j) — количество дней в процентном периоде i
    # по кредиту j, поделенное на количество дней в году, на который приходится окончание процентного периода i по кредиту j:
    end_dates_years = end_dates.astype(y_type)
    days_in_year = ((end_dates_years + year).astype(d_type) - end_dates_years.astype(d_type))
    periods_deltas = (end_dates - start_dates + day) / days_in_year

    # Формируем таблицу plan_monthly размером len(start_dates) x n, значение в ячейке (i,j) — доля от остатка основного долга на начало
    # процентного периода i по кредиту j, которая полагается быть погашенной согласно графику платежей в конце процентного периода i.
    # Расчет производится сначала для кредитов с аннуитетным типом платежа, затем — с дифференцированным:
    ann = payment_types == 0
    dif = payment_types == 1
    plan_monthly = np.empty(start_dates.shape)

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # В рамках расчета полагается, что при каждом частичном досрочном погашении заемщик выбирает сокращение аннуитета, а не текущего срока
    # до погашения (т.е. текущий срок до погашения не меняется)

    # Расчет помесячных погашений основного долга по графику платежей по каждому кредиту в каждом процентном периоде отдельно для кредитов
    # с аннуитетным типом платежа и отдельно для кредитов с диффиренцированным типом платежа (таблица plan_monthly):

    # — аннуитетный тип платежа:
    if np.any(ann):
        factors = np.power(1.0 + current_rates[ann] / 1200.0, periods_left[:, ann], where=periods_left[:, ann] > 0)
        plan_monthly[:, ann] = current_rates[ann] / 100.0 * (1.0 / 12.0 / (1.0 - 1.0 / factors) - periods_deltas[:, ann])

    # — дифференцированный тип платежа:
    if np.any(dif):
        plan_monthly[:, dif] = 1.0 / periods_left[:, dif]
        plan_monthly[np.isnat(end_dates)] = np.nan

    # При высоких ставках примененная выше формула аннуитета может привести к отрицательному плановому погашению. Исправим это:
    plan_monthly_corrected = np.minimum(np.maximum(plan_monthly, 0.0), 1.0)

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- РАСЧЕТ ПОМЕСЯЧНЫХ ДОСРОЧНЫХ ПОГАШЕНИЙ ОСНОВНОГО ДОЛГА ПО КАЖДОМУ КРЕДИТУ В КАЖДОМ ПРОЦЕНТНОМ ПЕРИОДЕ ------------------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    # Темп досрочных погашений CPR в дату платежа (i,j) в таблице end_dates рассчитывается исходя из:
    #     1. значения выдержки по кредиту в полных годах на дату начала процентного периода (i,j) в таблице start_dates;
    #     2. стимула к рефинансированию, посчитанного на основе разницы между текущей ставкой по кредиту j (current_rates) и ожидаемой
    #        ставкой рефинансирования ипотеки за месяц, предшествующий месяцу, на который приходится дата платежа (i,j) в таблице end_dates

    # Формируем таблицу loans_age размером len(start_dates) x n,
    # значение в ячейке (i,j) — количество полных лет, которые с даты выдачи прожил кредит j на начало процентного периода i:
    loans_age = np.floor((start_dates - issue_dates) / day / 365.0)

    # Формируем таблицу rates_monthly_avg размером len(start_dates) x n, значение в ячейке (i,j) — ожидаемая ставка рефинансирования
    # ипотеки за месяц, предшествующий месяцу, на который приходится дата платежа (i,j) в таблице end_dates.
    # Для начала необходимо определить минимальный месяц ставки рефинансирования ипотеки, которая нужна для расчета по данному ипотечному
    # покрытию (равен месяцу, предшествующему месяцу наименьшей даты платежа в end_dates):
    min_payment_month = np.nanmin(end_dates[0, :].astype(m_type))
    min_ref_rate_month = min_payment_month - month

    # Заготовка таблицы rates_monthly_avg:
    rates_monthly_avg = macroModel['ratesMonthlyAvg'].copy(deep=True)
    rates_monthly_avg = rates_monthly_avg[rates_monthly_avg['date'] >= min_ref_rate_month]['ref_rate'].values[:len(end_dates)]
    rates_monthly_avg = np.array([rates_monthly_avg, ] * n).transpose()
    # Для каждого кредита определяем разницу в месяцах между месяцем, на который приходится первая после даты среза выплаты по кредиту,
    # и минимальным месяцем выплаты в ипотечном покрытии:
    shifts = (end_dates[0, :].astype(m_type) - min_payment_month) / month
    # У тех кредитов, у которых месяц следующего после даты срезы платежа не равен минимальному месяцу выплаты в ипотечном покрытии,
    # сдвигаем ставки рефинансирования "назад":
    shifts_needed = shifts > 0
    while shifts_needed.any():
        last_array = rates_monthly_avg[-1, shifts_needed]
        rates_monthly_avg[:, shifts_needed] = np.vstack([rates_monthly_avg[1:, shifts_needed], last_array])
        shifts[shifts > 0] -= 1
        shifts_needed = shifts > 0

    # Определяем наибольший год жизни кредита, для которого определена S-кривая, и устанавливаем его на все последующие годы:
    max_cpr_model = float(s_curves['loanAge'].max())
    loans_age[loans_age > max_cpr_model] = max_cpr_model

    # Для каждого платежа по каждому кредиту определяем параметры S-кривой, по которой на этот платеж будет рассчитан CPR:
    s = loans_age.shape
    b0, b1, b2, b3, b4, b5, b6 = np.empty(s), np.empty(s), np.empty(s), np.empty(s), np.empty(s), np.empty(s), np.empty(s)

    for i in range(int(max_cpr_model) + 1):
        pos = loans_age == float(i)
        ind = s_curves['loanAge'] == i

        b0[pos] = s_curves[ind]['beta0'].values[0]
        b1[pos], b2[pos], b3[pos] = s_curves[ind]['beta1'].values[0], s_curves[ind]['beta2'].values[0], s_curves[ind]['beta3'].values[0]
        b4[pos], b5[pos], b6[pos] = s_curves[ind]['beta4'].values[0], s_curves[ind]['beta5'].values[0], s_curves[ind]['beta6'].values[0]

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # Для каждой даты платежа по каждому кредиту (i,j) в таблице end_dates рассчитываем ожидаемый стимул к рефинансированию:
    incentives = current_rates - rates_monthly_avg

    # Для каждой даты платежа по каждому кредиту (i,j) в таблице end_dates рассчитываем ожидаемый CPR:
    cpr = b0 + b1 * np.arctan(b2 + b3 * incentives) + b4 * np.arctan(b5 + b6 * incentives)

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # Для каждой даты платежа по каждому кредиту (i,j) в таблице end_dates рассчитываем размер досрочного погашения как долю
    # от остатка основного долга на начало процентного периода (за вычетом плановых погашений):
    cpr_factors = np.power(1.0 - cpr, 1.0 / 12.0, where=periods_left > 0)
    cpr_monthly = (1.0 - cpr_factors) * (1.0 - plan_monthly_corrected)

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- РАСЧЕТ ПОМЕСЯЧНЫХ ПОГАШЕНИЙ ПО КРЕДИТАМ В ИПОТЕЧНОМ ПОКРЫТИИ ----------------------------------------------------------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    # На основании заданного значения темпа выкупа дефолтов CDR рассчитываем постоянную долю от остатка основного долга на начало
    # процентного периода, которая будет приходиться на выкуп дефолтов у каждого кредита (иными словами, в отличие от CPR, CDR применяется
    # равномерно ко всем кредитам, т.е. ежемесячно определенная доля остатка основного долга по кредиту выкупается как дефолтная):
    cdr_monthly = 1.0 - (1.0 - cdr / 100.0) ** (1.0 / 12.0)

    # Умножение таблиц plan_monthly_corrected и cpr_monthly на (1.0 - cdr_monthly * 3.0) необходимо для того, чтобы учесть, что по доле
    # кредитов, равной cdr_monthly * 3.0, не будут поступать плановые и досрочные погашения. Eжемесячно в ипотечном покрытии находятся:
    #      а) кредиты, у которых количество дней просроченной задолженности составляет от 1  до 30 дней;
    #      б) кредиты, у которых количество дней просроченной задолженности составляет от 31 до 60 дней;
    #      в) кредиты, у которых количество дней просроченной задолженности составляет от 61 до 90 дней.
    # По кредитам из всех перечисленных групп нет погашений основного долга, доля каждой группы примерно равна cdr_monthly.
    plan_monthly_corrected *= (1.0 - cdr_monthly * 3.0)
    cpr_monthly *= (1.0 - cdr_monthly * 3.0)

    # Формируем таблицу amt_monthly размером len(start_dates) x n, значение в ячейке (i,j) — ожидаемая доля от основного долга на начало
    # процентного периода i по кредиту j, которая будет погашена в дату платежа (i,j) в таблице end_dates как сумма всех возможных видов
    # погашений (по графику, частичные и полные досрочные, выкупы дефолтов):
    amt_monthly = np.minimum(plan_monthly_corrected + cpr_monthly + cdr_monthly, 1.0)

    # Необходимо проверить, есть ли в ипотечном покрытии только что выданные кредиты, т.е. находящиеся по состоянию на дату среза в первом
    # процентном периоде. У таких кредитов первый процентный период может быть короче месяца. Полагается, что в таком случае в конце первого
    # процентного периода будут заплачены только проценты (погашений не будет, т.е. amt_monthly в первой строчке по таким кредитам = 0):
    new_loans = (start_dates[0, :] == issue_dates) & (end_dates[0, :] != maturity_dates)
    first_pay_period_length = ((end_dates[0, :] - start_dates[0, :]) / day + 1)
    first_month_length = ((start_dates[0, :].astype(m_type) + month).astype(d_type) - start_dates[0, :].astype(m_type)) / day
    short_first_period = first_pay_period_length < first_month_length
    amt_monthly[0, new_loans & short_first_period] = 0

    # Формируем таблицу amt_cml размером len(start_dates) x n, значение в ячейке (i,j) — доля от остатка основного долга по кредиту j на
    # дату среза, которая останется после платежа, осуществленного согласно таблице amt_monthly в конец процентного периода i:
    amt_cml = np.cumprod(1.0 - amt_monthly, axis=0)

    # Техническое уточнение amt_cml. Для кредитов, по которым производится моделирование до их текущей даты погашения (т.е. их текущая
    # дата погашения есть в таблице end_dates), необходимо приравнять нулю значение amt_cml в последнем процентном периоде, т.к. при
    # расчете amt_cml алгоритмы Python для последнего процентного периода дают число близкое, но не равное нулю:
    not_finished = np.nanmax(end_dates, axis=0) != maturity_dates
    zero_amt_cml_positions = np.count_nonzero(~np.isnat(start_dates[:, ~not_finished]), axis=0) - 1
    aux = amt_cml[:, ~not_finished]
    aux[zero_amt_cml_positions, np.arange(0, len(zero_amt_cml_positions))] = 0.0
    amt_cml[:, ~not_finished] = aux

    # Формируем таблицу end_debts размером len(start_dates) x n, значение в ячейке (i,j) — модельный остаток основного долга в рублях
    # по кредиту j после даты платежа процентного периода i (т.е. после даты платежа (i,j) в таблице end_dates):
    end_debts = current_debts * amt_cml

    # Формируем таблицу start_debts размером len(start_dates) x n, значение в ячейке (i,j) — модельный остаток основного долга в рублях
    # по кредиту j на начало процентного периода i (т.е. перед датой платежа (i,j) в таблице end_dates):
    start_debts = np.vstack([current_debts, end_debts[:-1]])

    # Формируем таблицу amt размером len(start_dates) x n, значение в ячейке (i,j) — модельное погашение остатка основного долга в рублях
    # по кредиту j (амортизация) в дату платежа процентного периода i (т.е. в дату платежа (i,j) в таблице end_dates):
    amt = start_debts - end_debts

    # Формируем таблицу yieldCoeffient размером len(start_dates) x n, значение в ячейке (i,j) — доля от остатка основного долга по кредиту
    # j на начало процентного периода i, по которой за процентный период не будут начислены и выплачены проценты. Состоит из двух слагаемых:
    #
    # Во-первых, так как полные или частичные погашения можно выплачивать в любой день процентного периода (как правило), то проценты за
    # оставшуюся часть этого процентного периода, начисленные на размер досрочного погашения, выплачены не будут. Полагается, что заемщики
    # производят досрочные погашения в середине процентного периода, поэтому первый компонент yieldCoeffient равен cpr_monthly / 2.0
    #
    # Во-вторых, при выкупе дефолта ДОМ.РФ не возвращает Ипотечному агенту проценты, начисленные по кредиту за количество дней между днем
    # выкупа и концом процентного периода, в котором происходит выкуп. Полагается, что ДОМ.РФ выкупает дефолтные кредиты в середине их
    # процентных периодов, поэтому второй компонент yieldCoeffient равен cdr_monthly / 2.0:
    yieldCoeffient = cpr_monthly / 2.0 + cdr_monthly / 2.0

    # Формируем таблицу yld размером len(start_dates) x n, значение в ячейке (i,j) — модельная выплата процентов в рублях
    # по кредиту j в дату платежа процентного периода i за процентный период i (т.е. в дату платежа (i,j) в таблице end_dates).
    yld = start_debts * (1.0 - yieldCoeffient) * current_rates / 100.0 * periods_deltas

    # В нескорректированной таблице plan_monthly отрицательные значения означают превышение значений yld над аннуитетами. Необходимо
    # вычесть из yld данные превышения (в реальности заемщики не могут платить больше аннуитета, излишки накапливаются и переносятся на
    # последний платеж, однако в данной модели применяется консервативных подход и излишки просто не учитываются):
    surpluses = plan_monthly < 0.0
    yld[surpluses] += start_debts[surpluses] * plan_monthly[surpluses]

    # Расчет начисленных процентов по каждому кредиту по состоянию на дату среза ипотечного покрытия:
    accrued_days = (reportDate - start_dates[:3] + 1) / day
    accrued_days[accrued_days < 0] = 0.0
    accrued_yld = np.nansum(current_debts * current_rates / 100.0 * accrued_days / (days_in_year[:3] / day), axis=0)

    # Для того, чтобы иметь возможность разбивать на составные части амортизацию ипотечного покрытия, формируем таблицы amt_cpr и amt_cdr.
    # Значение в ячейке (i,j) в таблицах — модельное погашение остатка основного долга в рублях по кредиту j (амортизация) в дату платежа
    # процентного периода i (т.е. в дату платежа (i,j) в таблице end_dates) в части досрочного погашения и части выкупа дефолтов
    # соответственно:
    cpr_fractions = cpr_monthly / amt_monthly
    cdr_fractions = cdr_monthly / amt_monthly

    amt_cpr = amt * cpr_fractions
    amt_cdr = amt * cdr_fractions

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    # ------------------------------------------------------------------------------------------------------------------------------------ #
    # ----- ГРУППИРОВКА ДЕНЕЖНЫХ ПОТОКОВ ПО КРЕДИТАМ В ПОМЕСЯЧНЫЕ ДЕНЕЖНЫЕ ПОТОКИ ПО ИПОТЕЧНОМУ ПОКРЫТИЮ --------------------------------- #
    # ------------------------------------------------------------------------------------------------------------------------------------ #

    # Даты платежей на одной строке таблицы end_dates могут приходиться на разные месяцы. Для того, что иметь возможность просуммировать
    # таблицы start_debts, amt, amt_plan, amt_cpr, yld по кредитам (т.е. просуммировать колонки) и получить помесячные суммы поступлений
    # по ипотечному покрытию, необходимо сдвинуть соответствующие потоки вперед на один месяц таким образом, чтобы одна строка в таблицах
    # start_debts, amt, amt_plan, amt_cpr, yld соответствовала одному и тому же месяцу:
    shift_cols = np.array(end_dates[0, :].astype(m_type) != reportDate.astype(m_type))
    nan_array = np.array([np.nan] * shift_cols.sum())

    # Сохранение оригинальной таблицы для расчета начислений на остаток на счете Ипотечного агента
    amt_base = copy.deepcopy(amt)
    yld_base = copy.deepcopy(yld)

    # Сдвиг платежей:
    amt[:, shift_cols] = np.vstack([nan_array, amt[:-1, shift_cols]])
    amt_cpr[:, shift_cols] = np.vstack([nan_array, amt_cpr[:-1, shift_cols]])
    amt_cdr[:, shift_cols] = np.vstack([nan_array, amt_cdr[:-1, shift_cols]])
    yld[:, shift_cols] = np.vstack([nan_array, yld[:-1, shift_cols]])
    cpr[:, shift_cols] = np.vstack([nan_array, cpr[:-1, shift_cols]])
    start_debts[:, shift_cols] = np.vstack([nan_array, start_debts[:-1, shift_cols]])

    # CPR по ипотечному покрытию за месяц считается как среднее значение CPR, взвешенное по остаткам основного долга по кредитам на начало
    # тех процентных периодов, выплата за которые приходится на этот месяц. Может быть такое, что в месяц, на который приходится дата среза
    # ипотечного покрытия, выпадают платежи только по части кредитов в ипотечном покрытии. В таком случае, алгоритм проводит расчет CPR за
    # этот месяц только по кредитам, которые имели платежи в этом месяце (посредством функции nansum):
    model_cpr = np.nansum(start_debts * cpr, axis=1) / np.nansum(start_debts, axis=1)

    # Формируем pay_months — последовательность месяцев от месяца, на который приходится дата среза, до месяца, на который смоделирован
    # последний денежный поток по ипотечному покрытию:
    pay_months = np.arange(reportDate.astype(m_type), reportDate.astype(m_type) + len(amt)).astype(m_type)

    # Поток по ипотечному покрытию формируется отдельно для части кредитов без субсидий (с фиксированной ставкой) и отдельно для части
    # кредитов с субсидиями (с плавающей ставкой):

    fxd_amt, flt_amt = None, None
    fxd_yld, flt_yld = None, None
    fxd_amt_cpr, flt_amt_cpr = None, None
    fxd_amt_cdr, flt_amt_cdr = None, None
    wa_deduction = None
    flt_fraction = None

    poolModel = {
        'fixed': None,  # Модельный помесячный денежный поток по ипотечному покрытию в части кредитов без субсидий
        'float': None,  # Модельный помесячный денежный поток по ипотечному покрытию в части кредитов с субсидиями
        'total': None,  # Модельный помесячный денежный поток по всему ипотечному покрытию
    }

    TEST_2, TEST_3 = None, None

    # Модельный денежный поток по кредитам в ипотечно покрытии по месяцам.
    # Независимо от типа ипотечного покрытия, денежные потоки задаются явно по части кредитов без субсидий и по части кредитов с субсидиями.
    # Если, например, в ипотечном покрытии нет кредитов с субсидиями, то таблица денежного потока 'float' будет содеражить нули.
    # Соответственно, если в ипотечном покрытии только кредиты с субсидиями, то таблица денежного потока 'fixed' будет содержать нули.
    for part, loans in zip(['fixed', 'float'], [fxd, flt]):

        poolModel[part] = {
            'debt': None,
            'cashflow': None,
            'accruedYield': None,
            'reinvestment': None,
        }

        part_debt = np.round(current_debts[loans].sum(), 2)
        part_amt = np.nansum(amt[:, loans], axis=1)
        part_yld = np.nansum(yld[:, loans], axis=1)
        part_amt_cpr = np.nansum(amt_cpr[:, loans], axis=1)
        part_amt_cdr = np.nansum(amt_cdr[:, loans], axis=1)

        part_model_cpr = np.nansum(start_debts[:, loans] * cpr[:, loans], axis=1) / np.nansum(start_debts[:, loans], axis=1)

        poolModel[part]['accruedYield'] = np.round(np.nansum(accrued_yld[loans]), 2)
        poolModel[part]['debt'] = part_debt
        poolModel[part]['cashflow'] = pd.DataFrame(
            {
                # Идентификатор, указывающий на то, что денежный поток по ипотечному покрытию является модельным:
                'model': [1] * len(pay_months),
                # Месяц, в который поступает денежный поток (без точного указания дней поступлений платежей по кредитам):
                'paymentMonth': pay_months,
                # Погашения основного долга по кредитам, поступившие в указанный месяц (по графику + досрочные + выкупы дефолтов), руб.:
                'amortization': saferound(part_amt, 2),
                # Досрочные погашения основного долга по кредитам, поступившие в указанный месяц, руб.:
                'prepayment': saferound(part_amt_cpr, 2),
                # Выкупы дефолтных кредитов из ипотечного покрытия в указанном месяце, руб.:
                'defaults': saferound(part_amt_cdr, 2),
                # Процентные поступления по кредитам, поступившие в указанный месяц, руб.:
                'yield': saferound(part_yld, 2),
                # Модельный CPR по платежам кредитов, поступившим в указанный месяц, % год.:
                'cpr': np.round(part_model_cpr * 100.0, 5),
            }
        )

        # Погашения основного долга по кредитам по графику платежей, поступившие в указанный месяц, руб.:
        poolModel[part]['cashflow']['scheduled'] = poolModel[part]['cashflow']['amortization'].values
        poolModel[part]['cashflow']['scheduled'] -= poolModel[part]['cashflow']['prepayment'].values
        poolModel[part]['cashflow']['scheduled'] -= poolModel[part]['cashflow']['defaults'].values

        # Корректировки согласно требованиям МСФО. По ряду Оригинаторов ипотечных покрытий ИЦБ ДОМ.РФ может возникнуть ситуация, что сумма
        # остатков основного долга в ипотечном покрытии согласно МСФО больше, чем в реальности. Это может быть в том случае, если последний
        # день предыдущего от reportDate месяца пришелся на выходной день (тогда платеж переносится на следующий месяц и остаток долга
        # по кредиту не уменьшается):
        if ifrs:

            # ifrsAmortization — сумма всех перенесенных с прошлого месяца погашений основного долга:
            poolModel[part]['cashflow']['amortizationIFRS'] = 0.0

            # ifrsYield — оценка суммы всех перенесенных с прошлого месяца процентных поступлений:
            poolModel[part]['cashflow']['yieldIFRS'] = 0.0

            # Продолжать имеет смысл только в том случае, если расчет проводится на основании отчета сервисного агента (в акте передачи
            # закладных поля currentDebtIFRS не может быть по определению):
            if poolData['currentDebtIFRS'] != [None]:

                current_debts_ifrs = np.array(poolData['currentDebtIFRS']).astype(float)
                part_debt_ifrs = np.round(np.sum(current_debts_ifrs[loans]), 2)
                part_amt_ifrs = np.round(part_debt_ifrs - part_debt, 2)

                if part_amt_ifrs > 1.0:
                    poolModel[part]['cashflow'].loc[0, 'amortizationIFRS'] = part_amt_ifrs

                    # Определяем кредиты, у которых произошел перенос платежа в следующий месяц:
                    difference = current_debts_ifrs - current_debts
                    transfer = loans & (difference > 1.0)

                    prev_month = (reportDate.astype(m_type) - month)
                    prev_month_year = prev_month.astype(y_type)
                    days_in_month = (reportDate - prev_month.astype(d_type)) / day
                    days_in_year = ((prev_month_year + year).astype(d_type) - prev_month_year.astype(d_type)) / day
                    period_delta = days_in_month / days_in_year

                    part_yield_ifrs = np.round(np.sum(current_debts_ifrs[transfer] * current_rates[transfer] / 100.0 * period_delta), 2)
                    poolModel[part]['cashflow'].loc[0, 'yieldIFRS'] = part_yield_ifrs

        # Сумма остатков основного долга по кредитам на начало месяца:
        poolModel[part]['cashflow']['debt'] = part_debt - poolModel[part]['cashflow']['amortization'].cumsum()
        poolModel[part]['cashflow']['debt'] += poolModel[part]['cashflow']['amortization']
        poolModel[part]['cashflow']['debt'] = np.round(poolModel[part]['cashflow']['debt'].values, 2)

        # WAC ипотечного покрытия на начало месяца:
        poolModel[part]['cashflow']['wac'] = np.nan
        if loans.any():
            # В первой строчке start_debts необходимо заполнить все пробелы, чтобы затем корректно считать средневзвешенные показатели:
            nan_debts = np.isnan(start_debts[0, :])
            start_debts[0, nan_debts] = start_debts[1, nan_debts]
            # Первая строка start_debts показывает остатки основного долга на reportDate, вторая — на 1 число месяца, след. за месяцем
            # reportDate и т.д.
            wac = np.nansum(start_debts[:, loans] * current_rates[loans], axis=1) / np.nansum(start_debts[:, loans], axis=1)
            length = len(poolModel[part]['cashflow'])
            poolModel[part]['cashflow']['wac'] = np.round(wac[:length], 5)

        # Для каждого месяца определяем значение Ключевой ставки, по которой будет произведен расчет субсидии по ипотечному покрытию.
        all_key_rates = macroModel['allKeyRates'].copy(deep=True)
        all_key_rates.rename(columns={'date': 'keyRateStartDate', 'key_rate': 'keyRate'}, inplace=True)
        poolModel[part]['cashflow'] = pd.merge_asof(poolModel[part]['cashflow'], all_key_rates, direction='backward',
                                                    left_on='paymentMonth', right_on='keyRateStartDate')

        poolModel[part]['cashflow']['waKeyRateDeduction'] = np.nan
        poolModel[part]['cashflow']['subsidy'] = 0.0
        if part == 'float' and loans.any():
            # В том случае, если в ипотечном покрытии есть субсидируемые кредиты, необходимо произвести расчет субсидий.
            # wa_deduction — среднезвзвешенный вычет на начало каждого месяца (первое значение — на reportDate)
            wa_deduction = np.nansum(start_debts[:, loans] * key_rate_deduction[loans], axis=1) / np.nansum(start_debts[:, loans], axis=1)
            length = len(poolModel[part]['cashflow'])
            poolModel[part]['cashflow']['waKeyRateDeduction'] = np.round(wa_deduction[:length], 5)

            # Рассчитываем размер начисленной субсидии за каждый месяц paymentMonth:
            subsidy_rates = poolModel[part]['cashflow']['keyRate'].values.reshape(-1, 1) + key_rate_deduction[loans]
            subsidy_values = yld[:, loans] / (current_rates[loans] / 100.0) * (subsidy_rates / 100.0)
            poolModel[part]['cashflow']['subsidy'] = saferound(np.nansum(subsidy_values, axis=1), 2)

        # Удаление лишних строк:
        stop_date = maturity_dates.max() if stop_date is None else stop_date
        extra_row = poolModel[part]['cashflow']['paymentMonth'].values.astype(m_type) > stop_date.astype(m_type)
        poolModel[part]['cashflow'] = poolModel[part]['cashflow'][~extra_row]

        # Ежедневные поступления амортизации, процентов и субсидий для дальнейшего расчета помесячных поступлений от начислений процентной
        # ставки на остаток на счете Ипотечного агента:
        if reinvestment:

            # Развертывание таблиц end_dates, amt, yld в одну колонку:
            part_end_dates = np.ravel(end_dates[:, loans], 'F')
            part_amt_base = np.ravel(amt_base[:, loans], 'F')
            part_yld_base = np.ravel(yld_base[:, loans], 'F')

            # Таблица поступлений на счет Ипотечного агента из различных источников по дням:
            poolModel[part]['reinvestment'] = pd.DataFrame({'date': part_end_dates,
                                                            'amt': part_amt_base,
                                                            'yld': part_yld_base}).dropna()

            # Добавление поступлений по субсидиям:
            poolModel[part]['reinvestment']['subsidy'] = 0.0
            poolModel[part]['reinvestment']['subsidyAccrualMonth'] = d_nat
            if part == 'float' and loans.any():

                # Техническая коррекция:
                part_cashflow = poolModel[part]['cashflow'].copy(deep=True)
                if initial_stop_date is not None:
                    part_cashflow = part_cashflow[part_cashflow['paymentMonth'] <= initial_stop_date]

                # subsidyPaymentDate — дата, в которую ожидается поступление субсидий за месяц paymentMonth:
                subsidy_payment_months = pd.DataFrame(part_cashflow['paymentMonth'].dt.month.values, columns=['accrualMonth'])
                subsidy_payment_months = subsidy_payment_months.merge(subsidy_months, how='left', on='accrualMonth')
                payment_months = part_cashflow['paymentMonth'].values.astype(m_type)
                subsidy_payment_dates = (payment_months + month * subsidy_payment_months['addMonths'].values).astype(d_type)
                subsidy_payment_dates += (subsidy_payment_day - 1) * day
                subsidies = pd.DataFrame({'date': subsidy_payment_dates,
                                          'subsidy': part_cashflow['subsidy'].values,
                                          'subsidyAccrualMonth': part_cashflow['paymentMonth'].values})

                poolModel[part]['reinvestment'] = pd.concat([poolModel[part]['reinvestment'], subsidies])
                poolModel[part]['reinvestment'][['amt', 'yld']] = poolModel[part]['reinvestment'][['amt', 'yld']].fillna(0.0)

            # Группирование поступлений по дням:
            c_group = ['date', 'subsidyAccrualMonth']
            poolModel[part]['reinvestment'] = poolModel[part]['reinvestment'].groupby(by=c_group, as_index=False, dropna=False).sum()
            poolModel[part]['reinvestment'].sort_values(by='date', inplace=True)

    # Расчет доли каждой части в ипотечном покрытии на всем модельном горизонте и сборка общего потока по ипотечному покрытию:
    poolModel['total'] = {
        'debt': np.round(poolModel['fixed']['debt'] + poolModel['float']['debt'], 2),
        'cashflow': None,
        'accruedYield': np.round(poolModel['fixed']['accruedYield'] + poolModel['float']['accruedYield'], 2),
    }

    fixed_debt = poolModel['fixed']['cashflow']['debt'].values
    float_debt = poolModel['float']['cashflow']['debt'].values
    total_debt = fixed_debt + float_debt
    poolModel['fixed']['cashflow']['fractionOfTotal'] = np.round(poolModel['fixed']['cashflow']['debt'].values / total_debt * 100.0, 25)
    poolModel['float']['cashflow']['fractionOfTotal'] = np.round(100.0 - poolModel['fixed']['cashflow']['fractionOfTotal'].values, 25)

    # Технически, для дальнейших расчетов, нужно указать, какая доля субсидируемой ипотеки находится в каждой части:
    poolModel['fixed']['cashflow']['floatFraction'] = 0.0
    poolModel['float']['cashflow']['floatFraction'] = 100.0

    poolModel['total']['cashflow'] = poolModel['float']['cashflow'].copy(deep=True)
    cols = ['amortization', 'prepayment', 'defaults', 'yield', 'scheduled', 'debt', 'subsidy']
    if ifrs:
        cols += ['amortizationIFRS', 'yieldIFRS']

    for c in cols:
        poolModel['total']['cashflow'][c] += poolModel['fixed']['cashflow'][c].values

    fixed_wac = poolModel['fixed']['cashflow']['wac'].fillna(0.0).values
    float_wac = poolModel['float']['cashflow']['wac'].fillna(0.0).values
    poolModel['total']['cashflow']['wac'] = np.round((fixed_wac * fixed_debt + float_wac * float_debt) / total_debt, 5)

    poolModel['total']['cashflow']['fractionOfTotal'] = 100.0
    poolModel['total']['cashflow']['floatFraction'] = poolModel['float']['cashflow']['fractionOfTotal'].values
    poolModel['total']['cashflow']['cpr'] = np.round(model_cpr[:len(poolModel['total']['cashflow'])] * 100.0, 5)

    if reinvestment:
        reinvestment_fixed = poolModel['fixed']['reinvestment']
        reinvestment_float = poolModel['float']['reinvestment']
        poolModel['total']['reinvestment'] = pd.concat([reinvestment_fixed, reinvestment_float])

        # Группирование поступлений по дням:
        c_group = ['date', 'subsidyAccrualMonth']
        poolModel['total']['reinvestment'] = poolModel['total']['reinvestment'].groupby(by=c_group, as_index=False, dropna=False).sum()
        poolModel['total']['reinvestment'].sort_values(by='date', inplace=True)

    # [ОБНОВЛЕНИЕ СТАТУСА РАСЧЕТА]
    current_percent += status_delta
    update(connection_id, current_percent, progress_bar)

    ########################################################################################################################################

    return {
        'poolStatistics': stats,
        'macroModel': macroModel,
        'poolModel': poolModel,
    }
