def apply_discount(items, code):
    total = 0
    for item in items:
        total += item["price"] * item["qty"]
    if code == "SAVE10":
        total = total - total * 0.1
    elif code == "SAVE20":
        total = total - total * 0.2
    return round(total, 2)
