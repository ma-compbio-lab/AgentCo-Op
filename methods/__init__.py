from methods import baseline, orchestrated_mas, sequential_mas

METHODS = {
    "baseline": baseline.run,
    "sequential": sequential_mas.run,
    "orchestrated": orchestrated_mas.run,
}
