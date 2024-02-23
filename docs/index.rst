Welcome to vibrio's documentation!
==================================
All normal use-cases should be available through a top-level import
(`from vibrio import ...`); see :mod:`vibrio`. Internal documentation is
provided at :mod:`vibrio.lazer` and :mod:`vibrio.types`, although use of these modules
should not be necessary.

Quickstart
----------
>>> from vibrio import HitStatistics, Lazer, OsuMod
>>> with Lazer() as lazer:
...     attributes = lazer.calculate_performance(
...         beatmap_id=1001682,
...         mods=[OsuMod.HIDDEN, OsuMod.DOUBLE_TIME],
...         hitstats=HitStatistics(
...             count_300=2019, count_100=104, count_50=0, count_miss=3, combo=3141
...         ),
...     )
...     attributes.total
1304.35

See :class:`vibrio.Lazer` (or :class:`vibrio.LazerAsync` for the asynchronous
implementation) for more details.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   vibrio
   vibrio.lazer
   vibrio.types


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
