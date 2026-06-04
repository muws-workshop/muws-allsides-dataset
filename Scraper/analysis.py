import json
from urllib.parse import urlparse
from collections import Counter, defaultdict
from itertools import combinations

filepath = "/nfs/home/abdullaha/qbias/Qbias/Scraper/output_2025_2026/allsides_jan2025_may2026_combined.jsonl"

def extract_domain(url):
    domain = urlparse(url).netloc
    if domain.startswith("www."):
        domain = domain[4:]
    return domain

overall_counter = Counter()
stance_counters = {"left": Counter(), "center": Counter(), "right": Counter()}
domain_stances = defaultdict(set)  # domain -> set of stances it appears in
cooccurrence_pairs = Counter()
cooccurrence_triples = Counter()
total_stories = 0
stories_with_all_three = 0

with open(filepath) as f:
    for line in f:
        d = json.loads(line)
        total_stories += 1

        story_domains = {}
        for stance in ["left", "center", "right"]:
            obj = d.get(stance)
            if obj and obj.get("link"):
                domain = extract_domain(obj["link"])
                if domain:
                    overall_counter[domain] += 1
                    stance_counters[stance][domain] += 1
                    domain_stances[domain].add(stance)
                    story_domains[stance] = domain

        # Co-occurrence analysis
        domains_in_story = list(story_domains.values())
        if len(domains_in_story) == 3:
            stories_with_all_three += 1
            triple = tuple(sorted(domains_in_story))
            cooccurrence_triples[triple] += 1

        if len(domains_in_story) >= 2:
            for pair in combinations(sorted(set(domains_in_story)), 2):
                cooccurrence_pairs[pair] += 1


print(f"Total stories: {total_stories}")
print(f"Stories with all 3 featured sources: {stories_with_all_three}")
print(f"Unique domains: {len(overall_counter)}")

print("\n" + "=" * 70)
print("1. OVERALL FREQUENCY DISTRIBUTION (Top 6)")
print("=" * 70)
for domain, count in overall_counter.most_common(6):
    pct = count / sum(overall_counter.values()) * 100
    print(f"  {domain:<40} {count:>5}  ({pct:.1f}%)")

print(f"\n  Total featured article slots: {sum(overall_counter.values())}")

print("\n" + "=" * 70)
print("2. STANCE-SPECIFIC DOMINANCE (Top 3 per stance)")
print("=" * 70)
for stance in ["left", "center", "right"]:
    total_in_stance = sum(stance_counters[stance].values())
    print(f"\n  --- {stance.upper()} (total: {total_in_stance}) ---")
    for domain, count in stance_counters[stance].most_common(3):
        pct = count / total_in_stance * 100
        print(f"    {domain:<40} {count:>5}  ({pct:.1f}%)")

print("\n" + "=" * 70)
print("3. CO-OCCURRENCE / PAIRING ANALYSIS")
print("=" * 70)

print("\n  --- Top 10 Domain Pairs (appearing together in the same story) ---")
for pair, count in cooccurrence_pairs.most_common(10):
    print(f"    {pair[0]} + {pair[1]:<30} {count:>5}")

print(f"\n  --- Top 10 Domain Triples (all 3 featured slots) ---")
for triple, count in cooccurrence_triples.most_common(10):
    print(f"    {triple[0]} + {triple[1]} + {triple[2]:<20} {count:>5}")

print("\n" + "=" * 70)
print("4. CROSS-STANCE MOBILITY")
print("=" * 70)
print("  Domains appearing in multiple stance categories:\n")

crossover_domains = {d: stances for d, stances in domain_stances.items() if len(stances) > 1}
crossover_sorted = sorted(crossover_domains.items(), key=lambda x: sum(overall_counter[x[0]] for _ in [1]), reverse=True)

if crossover_sorted:
    for domain, stances in crossover_sorted:
        stance_detail = []
        for s in ["left", "center", "right"]:
            if s in stances:
                stance_detail.append(f"{s}={stance_counters[s][domain]}")
        total = overall_counter[domain]
        print(f"    {domain:<40} total={total:>4}  [{', '.join(stance_detail)}]")
else:
    print("    No cross-stance domains found.")

print()
print("  Summary: domains that appear in ALL 3 stances:")
all_three = {d: s for d, s in domain_stances.items() if len(s) == 3}
if all_three:
    for domain in sorted(all_three, key=lambda d: overall_counter[d], reverse=True):
        detail = []
        for s in ["left", "center", "right"]:
            detail.append(f"{s}={stance_counters[s][domain]}")
        print(f"    {domain:<40} [{', '.join(detail)}]")
else:
    print("    None found.")

print("\n  Summary: domains that appear in exactly 2 stances:")
two_stances = {d: s for d, s in domain_stances.items() if len(s) == 2}
if two_stances:
    for domain in sorted(two_stances, key=lambda d: overall_counter[d], reverse=True)[:15]:
        stances = two_stances[domain]
        detail = []
        for s in ["left", "center", "right"]:
            if s in stances:
                detail.append(f"{s}={stance_counters[s][domain]}")
        print(f"    {domain:<40} [{', '.join(detail)}]")
