def generate_progress_bar(current, total, size=15):
    if current is None:
        current = 0
    if total is None or total == 0:
        total = 1
    percentage = current / total
    filled = int(size * percentage)
    bar = "▰" * filled + "▱" * (size - filled)
    return bar
