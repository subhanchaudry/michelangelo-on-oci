# Last 8.4 Router security patch; preserve its entrypoint and nonroot identity.
FROM container-registry.oracle.com/mysql/community-router@sha256:d704471c2bb78fa833790dc197959c004d09af5aa18f5f153ff40e7268f54043 AS runtime-security
USER 0:0
# Update only the OS libraries identified by the refreshed vulnerability scan.
# Do not upgrade mysql-router-community through a different product series.
RUN microdnf clean all && microdnf update -y openssl-libs gnutls libevent pcre2 curl libcurl libxml2 glib2 krb5-libs libacl sqlite-libs libcap && microdnf clean all
USER 999:999
