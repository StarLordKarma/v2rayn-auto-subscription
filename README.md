# v2rayN auto subscription

Separate project for MacBook Air M1 + v2rayN 7.24.8.

Every hour this repository:
- downloads `speed_tested.txt` from `luxxuria/harvester`;
- tests VLESS servers through Xray;
- checks general internet access, Gemini and Instagram reachability;
- detects the real exit country and prioritizes USA, UK, Germany, Switzerland, Netherlands, Norway;
- publishes `working.txt` and `working_base64.txt` for v2rayN subscription use.

Subscription URL for v2rayN:

`https://raw.githubusercontent.com/StarLordKarma/v2rayn-auto-subscription/main/working_base64.txt`

The older `StarLordKarma/vless-subscription` project is not used or modified by this workflow.
