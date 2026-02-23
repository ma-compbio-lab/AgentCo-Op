from methods import adaptive, baseline, orchestrated_mas, sequential_mas

METHODS = {
    "adaptive": adaptive.run,
    "baseline": baseline.run,
    "sequential": sequential_mas.run,
    "orchestrated": orchestrated_mas.run,
}
