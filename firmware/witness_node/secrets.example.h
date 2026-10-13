// Copy this file to secrets.h and fill it in. secrets.h is never committed or shared.
#pragma once

#define WIFI_SSID   "your-wifi-name"
#define WIFI_PASS   "your-wifi-password"

// The per-device secret: 32 random bytes, written as 64 hex characters. Generate one with
//     python3 -c "import secrets; print(secrets.token_hex(32))"
// and put the SAME value in the server's secrets file under this node's id. One secret per node:
// a node that is stolen or copied can be revoked on its own.
#define NODE_SECRET_HEX "0000000000000000000000000000000000000000000000000000000000000000"

// Only used when USE_TLS is 1: the PEM certificate of the CA that signed the server's certificate.
#define SERVER_CA_PEM "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n"
