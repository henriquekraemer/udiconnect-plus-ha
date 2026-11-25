# Security

This integration stores the e-mail and password of your Udiconnect Plus account in the
Home Assistant config entry and sends them to the vendor cloud over HTTPS. No other
service on the internet is contacted.

By default nothing is sent or received on the local network. With a local **Control
mode** (experimental), the integration listens on UDP 20911 and sends discovery, *stop*
and *move to position* frames to the motors on UDP 20910, without credentials. The
motors accept these frames unauthenticated from any device on the same network, whether
or not the integration is used.

## Reporting a vulnerability

Please do not open a public issue. Use
[private vulnerability reporting](https://github.com/henriquekraemer/udiconnect-plus-ha/security/advisories/new)
on GitHub. You should get a reply within a week.

Only the latest release is supported.
