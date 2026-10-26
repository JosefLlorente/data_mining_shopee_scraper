"""Open each Shopee URL on the USB-connected phone, read visible text via uiautomator, append to CSV.

Usage: python shopee.py urls [out.csv] [limit]   (urls: a file or folder; one URL per line; a folder's file names become the category)
Resumes: URLs already in out.csv are skipped. Stops on login/CAPTCHA screens; never tries to bypass them.
`python shopee.py retry shopee_out.csv` redoes only the retryable skipped URLs.
Anything not written to out.csv is logged with a reason in <out>_skipped.csv.
"""
import csv, glob, os, re, subprocess, sys, time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlparse

# Model inputs, the value to predict, and tracking columns
FEATURES = ["category", "price", "discount_pct", "rating", "review_count", "seller_rating", "seller_review_count",
            "seller_status", "availability", "shipping_fee", "original_price"]
TARGET = "sold_count"
METADATA = ["source_url", "collected_at", "status", "error_message", "raw_text"]
FIELDS = METADATA[:1] + FEATURES + [TARGET] + METADATA[1:]
SKIP_FIELDS = ["source_url", "category", "reason", "detail", "logged_at", "raw_text"]
BLOCK = re.compile(r"captcha|verify you are human|log ?in|sign ?in|security check", re.I)
PAUSE = 8  # shortcut: fixed wait for page load and pacing, upgrade to polling if pages load slower


def adb(*args):
    return subprocess.run(["adb", *args], capture_output=True, timeout=60).stdout


def load_urls(path):
    """path is a file or a folder of files; returns ([(url, category)], [(line, category, reason)]), category = file name."""
    files = sorted(glob.glob(os.path.join(path, "*"))) if os.path.isdir(path) else [path]
    seen, urls, rejected = set(), [], []
    for fp in files:
        cat = os.path.splitext(os.path.basename(fp))[0] if len(files) > 1 or os.path.isdir(path) else ""
        for line in open(fp, encoding="utf-8-sig"):
            u = line.split(",")[0].strip()
            p = urlparse(u)
            if not u or u == cat:  # blank line or the category header
                continue
            if not (p.scheme in ("http", "https") and "shopee" in p.netloc):
                rejected.append((u, cat, "invalid_url"))
            elif u in seen:
                rejected.append((u, cat, "duplicate"))
            else:
                seen.add(u); urls.append((u, cat))
    return urls, rejected


def screen_text(tries=3):
    for i in range(tries):  # dump sometimes comes back empty while the app is still redrawing
        out = adb("exec-out", "uiautomator", "dump", "/dev/tty").decode("utf-8", "ignore")
        xml = out[out.find("<?xml"): out.rfind("</hierarchy>") + 12]
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            if i == tries - 1: raise
            time.sleep(3)
            continue
        return [t for n in root.iter("node") if (t := (n.get("text") or n.get("content-desc") or "").strip())]


def parse(texts):
    j = "\n".join(texts)
    pick = lambda rx: (m.group(1) if (m := re.search(rx, j, re.I)) else "")
    # late-delivery credit and voucher-exclusive banners are not listing prices
    pj = re.sub(r"Get up to ₱\s*[\d,.]+|Exclusive Price:\s*₱\s*[\d,.]+", "", j)
    prices = list(dict.fromkeys(re.sub(r"\s", "", p) for p in re.findall(r"₱\s*[\d,]+(?:\.\d+)?", pj)))
    # shortcut: category and seller_review_count stay blank, need real screen samples to pattern-match; use raw_text meanwhile
    return {
        "price": prices[0] if prices else "",
        "original_price": prices[1] if len(prices) > 1 else "",
        "discount_pct": pick(r"(?:-(?=\d+%)|(?=\d+%\s*off))(\d+)%"),
        "rating": pick(r"(?m)^(\d\.\d)$"),
        "review_count": pick(r"Product Ratings\n\(([\d.,]+k?)\)"),
        "seller_rating": pick(r"(\d\.\d)\nRating\n"),
        "seller_status": pick(r"(Shopee Mall|Star\+? Seller|Preferred(?: Seller)?)"),
        "availability": "out_of_stock" if re.search(r"out of stock|sold out|unavailable", j, re.I) else ("available" if prices else ""),
        "shipping_fee": "free" if re.search(r"free shipping", j, re.I) else pick(r"shipping[^₱\n]{0,20}(₱\s?[\d,.]+)"),
        "sold_count": pick(r"([\d.,]+k?)\s*sold"),
        "raw_text": " | ".join(texts)[:3000],
    }


def main(inp, out="shopee_out.csv", limit=None):
    sys.stdout.reconfigure(errors="replace")  # Windows console can't print ₱
    skipped = os.path.splitext(out)[0] + "_skipped.csv"
    read = lambda p: list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.exists(p) else []
    def compact():  # skipped log keeps only still-unresolved URLs, newest entry each
        if not os.path.exists(skipped): return
        saved, rows = {r["source_url"] for r in read(out)}, read(skipped)
        fixed = ("duplicate", "invalid_url")  # input-file notes, not retryable
        last = {r["source_url"]: r for r in rows if r["reason"] not in fixed}
        keep = [r for r in rows if r["reason"] in fixed] + [r for u, r in last.items() if u not in saved]
        with open(skipped, "w", newline="", encoding="utf-8") as h:
            hw = csv.DictWriter(h, SKIP_FIELDS); hw.writeheader(); hw.writerows(keep)
    compact()
    # dead listings never come back, so they are not retried; blocked/error/no-price are
    done = {r["source_url"] for r in read(out)} | {r["source_url"] for r in read(skipped) if r["reason"] == "unavailable"}
    try:
        devices = adb("devices")
    except FileNotFoundError:
        sys.exit("adb not found on PATH. Install Android Platform Tools and reopen the terminal.")
    if b"\tdevice" not in devices:
        sys.exit("No authorized device. Check USB debugging and run `adb devices`.")
    if inp == "retry":  # redo only what the skipped log says is worth another try
        retry = {"empty_screen", "error", "no_price_found", "blocked"}
        urls, rejected = list(dict.fromkeys((r["source_url"], r["category"]) for r in read(skipped) if r["reason"] in retry)), []
    else:
        urls, rejected = load_urls(inp)
    new_out, new_skip = not os.path.exists(out), not os.path.exists(skipped)
    with open(out, "a", newline="", encoding="utf-8") as f, open(skipped, "a", newline="", encoding="utf-8") as g:
        w, sk = csv.DictWriter(f, FIELDS), csv.DictWriter(g, SKIP_FIELDS)
        if new_out: w.writeheader()
        if new_skip:  # input-file rejects are logged once, with the first run
            sk.writeheader()
            for u, cat, reason in rejected:
                sk.writerow({"source_url": u, "category": cat, "reason": reason, "logged_at": datetime.now().isoformat(timespec="seconds")})
        def skip(u, cat, reason, detail="", raw=""):
            sk.writerow({"source_url": u, "category": cat, "reason": reason, "detail": detail,
                         "logged_at": datetime.now().isoformat(timespec="seconds"), "raw_text": raw})
            g.flush(); print(f"    skipped: {reason} {detail}", flush=True)
        todo = [(u, c) for u, c in urls if u not in done]
        batch = todo[: int(limit) if limit else None]
        for n, (u, cat) in enumerate(batch, 1):
            print(f"[{n}/{len(batch)}] {cat}: {u[:70]}", flush=True)
            try:
                adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"'{u}'")
                time.sleep(PAUSE)
                texts = screen_text()
                if any("does not exist" in t for t in texts):
                    skip(u, cat, "unavailable", "listing does not exist")
                    time.sleep(PAUSE)
                    continue
                adb("shell", "input", "swipe", "540", "1900", "540", "700", "400")  # rating/sold sit below the first screen
                time.sleep(2)
                texts += [t for t in screen_text() if t not in texts]
                raw = " | ".join(texts)[:3000]
                if BLOCK.search(" ".join(texts)[:500]):
                    skip(u, cat, "blocked", "login/CAPTCHA screen, stopped", raw)
                    sys.exit(f"Blocked at {u}. Resolve manually; rerun to resume.")
                if not texts:
                    skip(u, cat, "empty_screen", "no text read from the phone")
                elif not (data := parse(texts))["price"]:
                    skip(u, cat, "no_price_found", "page read but no price pattern matched", raw)
                else:
                    w.writerow({"source_url": u, "category": cat, "collected_at": datetime.now().isoformat(timespec="seconds"),
                                "status": "ok", **data})
                    f.flush()  # save after every product
                    print(f"    ok price={data['price']} sold={data['sold_count']}", flush=True)
            except (subprocess.TimeoutExpired, ET.ParseError) as e:
                skip(u, cat, "error", repr(e))
            time.sleep(PAUSE)
    compact()
    print(f"Done. Skipped items and reasons: {skipped}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
