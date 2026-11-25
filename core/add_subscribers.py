#!/usr/bin/env python3
"""Add N consecutive test subscribers to the local free5GC webui (default
test account of free5gc-compose). Same key/OPc as config/uecfg.yaml."""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:5000/api"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
FIRST = "imsi-208930000000001"
PLMN = "20893"


def req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Token", token)
    with urllib.request.urlopen(r, timeout=10) as resp:
        txt = resp.read().decode()
        return resp.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)


st, login = req("POST", "/login", {"username": "admin", "password": "free5gc"})
token = login.get("access_token") if isinstance(login, dict) else None
assert token, f"login failed: {st}"

snssai = {"sst": 1, "sd": "010203"}
body = {
    "userNumber": N,
    "ueId": FIRST,
    "plmnID": PLMN,
    "AuthenticationSubscription": {
        "authenticationManagementField": "8000",
        "authenticationMethod": "5G_AKA",
        "milenage": {"op": {"encryptionAlgorithm": 0, "encryptionKey": 0, "opValue": ""}},
        "opc": {"encryptionAlgorithm": 0, "encryptionKey": 0,
                "opcValue": "8e27b6af0e692e750f32667a3b14605d"},
        "permanentKey": {"encryptionAlgorithm": 0, "encryptionKey": 0,
                         "permanentKeyValue": "8baf473f2f8fd09487cccbd7097c6862"},
        "sequenceNumber": "000000000023",
    },
    "AccessAndMobilitySubscriptionData": {
        "gpsis": ["msisdn-"],
        "nssai": {"defaultSingleNssais": [snssai], "singleNssais": []},
        "subscribedUeAmbr": {"downlink": "2 Gbps", "uplink": "1 Gbps"},
    },
    "SessionManagementSubscriptionData": [{
        "singleNssai": snssai,
        "dnnConfigurations": {"internet": {
            "pduSessionTypes": {"defaultSessionType": "IPV4", "allowedSessionTypes": ["IPV4"]},
            "sscModes": {"defaultSscMode": "SSC_MODE_1",
                         "allowedSscModes": ["SSC_MODE_2", "SSC_MODE_3"]},
            "5gQosProfile": {"5qi": 9, "arp": {"priorityLevel": 8,
                                               "preemptCap": "", "preemptVuln": ""},
                             "priorityLevel": 8},
            "sessionAmbr": {"uplink": "1000 Mbps", "downlink": "1000 Mbps"},
        }},
    }],
    "SmfSelectionSubscriptionData": {
        "subscribedSnssaiInfos": {"01010203": {"dnnInfos": [{"dnn": "internet"}]}}},
    "AmPolicyData": {"subscCats": ["free5gc"]},
    "SmPolicyData": {"smPolicySnssaiData": {"01010203": {
        "snssai": snssai, "smPolicyDnnData": {"internet": {"dnn": "internet"}}}}},
    "FlowRules": [], "QosFlows": [], "ChargingDatas": [],
}
st, out = req("POST", f"/subscriber/{FIRST}/{PLMN}/{N}", body, token)
print("create:", st, str(out)[:200])
st, subs = req("GET", "/subscriber", token=token)
print("subscribers:", len(subs) if isinstance(subs, list) else subs)
