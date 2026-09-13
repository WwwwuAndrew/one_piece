import tushare as ts
from datetime import datetime, timedelta

TOKEN = "aa215f4d32e50515e91cb416e282ed610a1728ab0e3d8765511c1809"

ts.set_token(TOKEN)
pro = ts.pro_api()

# 往前取约一个月，足够覆盖最近15个交易日
end_date = datetime.now().strftime("%Y%m%d")
start_date = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")

df = pro.daily(
    ts_code="000001.SZ",
    start_date=start_date,
    end_date=end_date,
)

df = (
    df.sort_values("trade_date", ascending=False)
      .head(15)
      .reset_index(drop=True)
)

print(df.to_string(index=False))
