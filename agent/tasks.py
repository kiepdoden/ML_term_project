"""50 tau-bench-style analytics tasks over TPC-H (+ the KV lookup tool).

10 templates x 5 parameter sets. Each task has an id (template-k) so the
train/test split can be done by task and, more strictly, by template.
"""
REGIONS = ["ASIA", "EUROPE", "AMERICA", "AFRICA", "MIDDLE EAST"]
NATIONS = ["GERMANY", "JAPAN", "BRAZIL", "KENYA", "SAUDI ARABIA"]
SEGMENTS = ["BUILDING", "AUTOMOBILE", "MACHINERY", "HOUSEHOLD", "FURNITURE"]
YEARS = [1993, 1994, 1995, 1996, 1997]
MODES = ["AIR", "SHIP", "TRUCK", "RAIL", "MAIL"]
TYPES = ["BRASS", "STEEL", "COPPER", "TIN", "NICKEL"]
MONTHS = ["March", "June", "September", "November", "January"]

TEMPLATES = [
    ("t01", "Which 5 suppliers in {region} had the highest revenue (extendedprice * (1 - discount)) "
            "in {year}, and what are their phone numbers?",
     lambda i: dict(region=REGIONS[i], year=YEARS[i])),
    ("t02", "What was the total revenue of orders placed by customers in the {segment} market "
            "segment in {month} {year}?",
     lambda i: dict(segment=SEGMENTS[i], month=MONTHS[i], year=YEARS[(i + 2) % 5])),
    ("t03", "Find the customer in {nation} with the largest account balance, give their name and "
            "phone, and list their 3 most recent orders with dates and totals.",
     lambda i: dict(nation=NATIONS[i])),
    ("t04", "For parts of type containing '{ptype}', which supplier nation had the lowest average "
            "supply cost? Give the nation and the average.",
     lambda i: dict(ptype=TYPES[i])),
    ("t05", "How many line items were shipped by {mode} in {year} and arrived after the commit "
            "date? What share of all {mode} line items of that year is that?",
     lambda i: dict(mode=MODES[i], year=YEARS[(i + 1) % 5])),
    ("t06", "List the 3 nations in {region} with the most customers, and for the top one give the "
            "number of orders its customers placed in {year}.",
     lambda i: dict(region=REGIONS[(i + 1) % 5], year=YEARS[(i + 3) % 5])),
    ("t07", "Look up customer {ckey}: what is their market segment and nation, how many orders "
            "have they placed in total, and what is their average order total?",
     lambda i: dict(ckey=[42, 1234, 77777, 98765, 149999][i])),
    ("t08", "Which order placed in {year} had the largest number of line items? Give the order "
            "key, the customer name and the order total.",
     lambda i: dict(year=YEARS[(i + 4) % 5])),
    ("t09", "Look up supplier {skey}: which nation is it in, how many distinct parts does it "
            "supply, and what is its total available quantity?",
     lambda i: dict(skey=[7, 321, 4567, 8888, 9999][i])),
    ("t10", "What was the monthly revenue in {year} for {segment} customers in {region}? "
            "Which month was the highest?",
     lambda i: dict(year=YEARS[(i + 2) % 5], segment=SEGMENTS[(i + 1) % 5],
                    region=REGIONS[(i + 2) % 5])),
]


def all_tasks():
    out = []
    for tid, text, params in TEMPLATES:
        for i in range(5):
            out.append({"task": f"{tid}-{i}", "template": tid,
                        "prompt": text.format(**params(i))})
    return out


if __name__ == "__main__":
    for t in all_tasks():
        print(t["task"], "|", t["prompt"])
