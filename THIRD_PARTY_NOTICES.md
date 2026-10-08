# Сторонние компоненты

OlegPainter распространяется по лицензии GNU GPL v3.0 ([LICENSE](LICENSE)) и использует перечисленные ниже компоненты. Каждый из них остаётся под своей лицензией.

## Библиотеки Python (входят в сборку)

| Компонент | Лицензия | Сайт |
| --- | --- | --- |
| PySide6 / Qt for Python | LGPL-3.0 | https://www.qt.io/qt-for-python |
| NumPy | BSD-3-Clause | https://numpy.org |
| SciPy | BSD-3-Clause | https://scipy.org |
| Pillow | MIT-CMU (HPND) | https://python-pillow.org |
| OpenCV (opencv-python) | Apache-2.0 | https://opencv.org |
| scikit-learn | BSD-3-Clause | https://scikit-learn.org |
| Numba | BSD-2-Clause | https://numba.pydata.org |
| ONNX Runtime (DirectML) | MIT | https://onnxruntime.ai |
| windows-capture | MIT ([текст](docs/licenses/windows-capture-MIT.txt)) | https://github.com/NiiightmareXD/windows-capture |
| interception-python | MIT | https://github.com/kennyhml/pyinterception |
| keyboard | MIT | https://github.com/boppreh/keyboard |
| pynput | LGPL-3.0 | https://github.com/moses-palmer/pynput |
| pywin32 | PSF-2.0 | https://github.com/mhammond/pywin32 |

Компоненты под LGPL-3.0 используются как отдельные библиотеки и могут быть заменены пользователем на другие совместимые версии.

## Драйвер Interception (не входит в OlegPainter)

Драйвер ввода [Interception](https://github.com/oblitum/Interception) — проект Francisco Lopes. Исходный код OlegPainter не содержит и не изменяет код драйвера. По лицензии автора для некоммерческого использования драйвер и его установщик распространяются под LGPL-3.0; коммерческое использование требует отдельной лицензии автора.

OlegPainter распространяется бесплатно. Готовая сборка драйвер не содержит. По просьбе пользователя программа скачивает **неизменённый** официальный архив автора с его страницы релизов на GitHub, проверяет контрольную сумму SHA-256 и запускает из него установщик — только после явного согласия пользователя. Если положить официальный установщик рядом с программой (папка `drivers/interception`), программа использует его, проверив ту же контрольную сумму. Мы не отвечаем за работу драйвера и его ошибки — подробности в [инструкции по установке](docs/INSTALL.md#драйвер-мыши-interception).

## ИИ-модели (не входят в OlegPainter)

Модели скачиваются только по выбору пользователя, с проверкой контрольной суммы SHA-256. Источники и лицензии записаны в каталоге [engine/ai/catalog.json](engine/ai/catalog.json):

| Модель | Лицензия | Источник |
| --- | --- | --- |
| U²-Net (U²-Netp) | Apache-2.0 | https://github.com/xuebinqin/U-2-Net |
| ISNet (DIS) | Apache-2.0 | https://github.com/xuebinqin/DIS |
| BiRefNet | MIT | https://github.com/ZhengPeng7/BiRefNet |
| SAM 2.1 | Apache-2.0 | https://github.com/facebookresearch/sam2 |
| Depth Anything V2 (Small) | Apache-2.0 | https://github.com/DepthAnything/Depth-Anything-V2 |
| Informative Drawings | MIT | https://github.com/carolineec/informative-drawings |
| LaMa | Apache-2.0 | https://github.com/advimman/lama |
| Real-ESRGAN | BSD-3-Clause | https://github.com/xinntao/Real-ESRGAN |

## Значки и изображения

Значки интерфейса (`assets/icons`) и значок программы созданы для OlegPainter и распространяются вместе с ним по GPL-3.0.
