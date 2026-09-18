# SPA is gitignored at web/dist; build it in a node stage and copy only the output.
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-910b8e3ba8980e20faae9f37dcaca0ea9d8bd9ae@sha256:f4739b6e74309dcccd087792949fd613191db7f33d33109c78127684dcb5dd73

COPY --chmod=0644 runtime/persona.md /opt/hermes/plow-seed/persona.md
COPY LICENSE /usr/share/doc/phoneflow/LICENSE

COPY pf-run/    /opt/hermes/skills/pf-run/
COPY pf-mirror/ /opt/hermes/skills/pf-mirror/
COPY pf-setup/  /opt/hermes/skills/pf-setup/
COPY pf-hints/  /opt/hermes/skills/pf-hints/
COPY --chown=10000:10000 pf-run/    /var/lib/hermes/skills/pf-run/
COPY --chown=10000:10000 pf-mirror/ /var/lib/hermes/skills/pf-mirror/
COPY --chown=10000:10000 pf-setup/  /var/lib/hermes/skills/pf-setup/
COPY --chown=10000:10000 pf-hints/  /var/lib/hermes/skills/pf-hints/
COPY pf_api/    /opt/phoneflow/pf_api/
COPY pf_mirror/ /opt/phoneflow/pf_mirror/
COPY --from=web /web/dist/ /opt/phoneflow/web/
COPY workflows/ /opt/phoneflow/workflows/
COPY app_hints/ /opt/phoneflow/app_hints/
# Mac helper sources and their prebuilt universal binaries (mac/build.sh); the
# driver ships them to the owner's Mac through Latch.
COPY mac/       /opt/phoneflow/mac/

RUN find /opt/hermes/skills -mindepth 1 -type d -exec chmod 0755 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} + \
 && install -d -m 0755 /opt/phoneflow \
 && find /opt/phoneflow -type d -exec chmod 0755 {} + \
 && find /opt/phoneflow -type f -exec chmod 0644 {} +

COPY vendor/client.pin /opt/plow/agent-index-client.pin
RUN set -eu; \
    sha="$(sed -n 's/^sha=//p' /opt/plow/agent-index-client.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/agent-index-client.pin)"; \
    path="$(sed -n 's/^path=//p' /opt/plow/agent-index-client.pin)"; \
    curl -fsS --max-time 60 -o /opt/plow/agent-index-client.py \
      "https://raw.githubusercontent.com/plow-pbc/agent-index-client/${sha}/${path}"; \
    got="$(sha256sum /opt/plow/agent-index-client.py | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "agent-index client is $got, pin says $want" >&2; exit 1; }; \
    chmod 0644 /opt/plow/agent-index-client.py

COPY image/s6-overlay/ /etc/s6-overlay/
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/phoneflow
