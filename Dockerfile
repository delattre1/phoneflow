# SPA is gitignored at web/dist; build it in a node stage and copy only the output.
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-ef0019372ff8bca593611b31ebd2e08f9f1458ff@sha256:a8a2f97ad78b8192d80a984dce81d3bf5a9a883d18cb7b677704913a09b56aee

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

COPY image/s6-overlay/ /etc/s6-overlay/
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/phoneflow
