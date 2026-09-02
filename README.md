# v2rayN auto subscription

Separate project for MacBook Air M1 + v2rayN 7.24.8.

Every hour this repository:
- downloads `speed_tested.txt` from `luxxuria/harvester`;
- tests VLESS servers through Xray;
- checks general internet access, Gemini and ChatGPT/OpenAI reachability;
- prioritizes USA, UK, Germany, Switzerland, Netherlands, Norway;
- publishes `working.txt` and `working_base64.txt` for v2rayN subscription use.

The older `StarLordKarma/vless-subscription` project is not used or modified by this workflow.
