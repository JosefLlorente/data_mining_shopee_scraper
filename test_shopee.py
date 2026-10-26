import os, tempfile
from shopee import parse, load_urls

p = parse(["Nice Widget 500ml Stainless", "₱1,299.50", "4.8", "2.3k sold"])
assert p["price"] == "₱1,299.50" and p["sold_count"] == "2.3k" and p["rating"] == "4.8", p
q = parse(["Widget", "₱99", "₱199", "50% off", "Product Ratings", "(1.2k)", "Free Shipping", "Shopee Mall"])
assert (q["original_price"], q["discount_pct"], q["review_count"], q["shipping_fee"], q["seller_status"], q["availability"]) == ("₱199", "50", "1.2k", "free", "Shopee Mall", "available"), q
assert parse([])["price"] == ""  # missing fields stay blank

f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
f.write("https://shopee.ph/a-i.1.2\nhttps://shopee.ph/a-i.1.2\nnot a url\nhttps://evil.com/x\n"); f.close()
urls, rej = load_urls(f.name)
assert urls == [("https://shopee.ph/a-i.1.2", "")], urls
assert [r[2] for r in rej] == ["duplicate", "invalid_url", "invalid_url"], rej
os.unlink(f.name)
print("ok")
