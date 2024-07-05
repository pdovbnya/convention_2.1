Для того, чтобы произвести расчет, необходимо запустить скрипт run.py, предварительно задав в начале скрипта путь, куда будет сохраняться Excel-файл c результатом расчета, а также выделив в листе calculations интересуемые выпуски ИЦБ ДОМ.РФ. При выборе нескольких выпусков скрипт запустит расчеты calculations последовательно, при этом каждый расчет будет сопровождаться отдельной строкой прогресса (progress bar) в консоли. По завершении последнего расчета результаты всех расчетов будет собраны в одном Excel-файле и сохранены по указанному в начале скрипта пути

Основной скрипт Конвенции – convention.py. В нём расписан объект Convention, последовательное изучение/чтение комментариев в котором сформирует у пользователя достаточное представление о работе модели. На первом шаге необходимо изучить метод __init__(). После инициализации объекта Convention, непосредственно сам расчет запускается методом calculate(). Данный метод последовательно запускает четыре процесса, которые необходимо изучать в следующем порядке:
•	poolCashflowModel		– расчет денежного потока по ипотечному покрытию
•	mbsCashflowModel		– расчет денежного потока по ИЦБ ДОМ.РФ
•	mbsPricing			– расчет ценовых метрик ИЦБ ДОМ.РФ
•	outputPreparation		– подготовка выходных данных расчета

Метод poolCashflowModel запускает модель денежного потока по ипотечному покрытию из файла pool_model.py. В свою очередь, в рамках модели ипотечного покрытия запускается модель расчете ожидаемой траектории ставки рефинансирования ипотеки из скрипта macro_model.py. В скрипте auxiliary.py прописаны технические функции, классы и переменные, которые используются в основных скриптах модели
