
FINE_PER_DAY = 0.50

def calculate_fine(days_late):
    return max(0, days_late) * FINE_PER_DAY
