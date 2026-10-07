"""Keep reconstructed public image assets readable by their nonroot users."""


def prepare(source):
    # These are upstream public defaults, never the generated deployment
    # configuration or credentials, which retain their private permissions.
    for name in ('apiserver', 'controllermgr', 'worker'):
        directory = source / 'go/cmd' / name / 'config'
        directory.chmod(0o755)
        for asset in directory.rglob('*'):
            if asset.is_symlink():
                raise RuntimeError('Public service configuration contains a symlink')
            asset.chmod(0o755 if asset.is_dir() else 0o644)
    # Private host umask also makes freshly compiled Go binaries mode 0700.
    # Set the image's public executable mode explicitly at COPY, independently
    # of host permissions, for the distroless nonroot runtime.
    service_dockerfile = source / 'docker/service.Dockerfile'
    service_content = service_dockerfile.read_text()
    plain_copy = 'COPY $BINARY_PATH /app'
    executable_copy = 'COPY --chmod=0755 $BINARY_PATH /app'
    if plain_copy in service_content:
        if service_content.count(plain_copy) != 1:
            raise RuntimeError('Upstream service Dockerfile binary copy is ambiguous')
        service_dockerfile.write_text(service_content.replace(plain_copy, executable_copy))
    elif service_content.count(executable_copy) != 1:
        raise RuntimeError('Upstream service Dockerfile has an unexpected binary copy')
    # The reviewed helper generates dist after this function runs. Normalize
    # inside the reconstructed Dockerfile so every fresh build remains correct
    # even when the installer protects its host files with umask 077.
    dockerfile = source / 'docker/ui-prebuilt.Dockerfile'
    content = dockerfile.read_text()
    marker = '# OCI installer: public static assets are readable by nonroot nginx.'
    if marker not in content:
        copy = 'COPY javascript/app/dist /usr/share/nginx/html'
        if content.count(copy) != 1:
            raise RuntimeError('Upstream UI Dockerfile has an unexpected asset copy')
        content = content.replace(copy, copy + '\n' + marker + '\n'
            'RUN find /usr/share/nginx/html -type d -exec chmod 0755 {} + && \\\n'
            '    find /usr/share/nginx/html -type f -exec chmod 0644 {} +')
        dockerfile.write_text(content)
