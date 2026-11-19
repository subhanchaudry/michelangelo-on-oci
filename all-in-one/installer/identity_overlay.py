"""Apply authenticated API/workload identity after the reviewed source is reconstructed."""
from pathlib import Path


def replace(path, old, new, marker=None):
    text = path.read_text()
    if (marker or new) in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"Identity overlay source mismatch: {path.name}")
    path.write_text(text.replace(old, new))


def prepare_source(source):
    source = Path(source)
    overlay = Path(__file__).parent / "identity"
    for path in overlay.glob("*.go"):
        target = source / "go/auth" / path.name
        target.write_bytes(path.read_bytes())
    main = source / "go/cmd/apiserver/main.go"
    replace(main, "auth.AuthModule", "auth.PrincipalAuthModule")
    dispatcher = source / "go/cmd/apiserver/yarpc.go"
    replace(dispatcher, '"context"\n', '"context"\n\t"github.com/michelangelo-ai/michelangelo/go/auth"\n', '"github.com/michelangelo-ai/michelangelo/go/auth"')
    replace(dispatcher, "\t\tName: serverName,", "\t\tName: serverName,\n\t\tInboundMiddleware: yarpc.InboundMiddleware{Unary: auth.NewIdentityMiddleware()},", 'auth.NewIdentityMiddleware()')
    worker = source / "go/worker/config.go"
    replace(worker, '"crypto/tls"\n', '"crypto/tls"\n\t"github.com/michelangelo-ai/michelangelo/go/auth"\n', '"github.com/michelangelo-ai/michelangelo/go/auth"')
    replace(worker, "\t\tName:      p.Config.MaAPIServiceName,", "\t\tName:      p.Config.MaAPIServiceName,\n\t\tOutboundMiddleware: yarpc.OutboundMiddleware{Unary: auth.ServiceTokenMiddleware{}},", 'auth.ServiceTokenMiddleware{}')
    handler = source / "go/api/handler/handler.go"
    replace(handler, '"context"\n', '"context"\n\t"github.com/michelangelo-ai/michelangelo/go/auth"\n', '"github.com/michelangelo-ai/michelangelo/go/auth"')
    replace(handler, 'func (handler *apiHandler) Create(ctx context.Context, obj ctrlRTClient.Object, opts *metav1.CreateOptions) error {', 'func (handler *apiHandler) Create(ctx context.Context, obj ctrlRTClient.Object, opts *metav1.CreateOptions) error {\n\tif err := auth.StampIdentity(ctx, obj, true); err != nil { return err }', 'auth.StampIdentity(ctx, obj, true)')
    replace(handler, 'func (handler *apiHandler) Update(ctx context.Context, obj ctrlRTClient.Object, opts *metav1.UpdateOptions) error {', '''func (handler *apiHandler) Update(ctx context.Context, obj ctrlRTClient.Object, opts *metav1.UpdateOptions) error {
    original := obj.DeepCopyObject().(ctrlRTClient.Object)
    if err := handler.Get(ctx, obj.GetNamespace(), obj.GetName(), &metav1.GetOptions{}, original); err != nil { return err }
    auth.PreserveOwner(obj, original)
    if err := auth.StampIdentity(ctx, obj, false); err != nil { return err }''', 'auth.PreserveOwner(obj, original)')
    replace(handler, 'initLog := log.WithValues("action", action, "namespace", namespace, "name", name, "kind", kind)', '''initLog := log.WithValues("action", action, "namespace", namespace, "name", name, "kind", kind)
    if principal, verified := auth.GetPrincipal(ctx); verified {
        initLog = initLog.WithValues("actorID", principal.ID, "actorSubject", principal.Subject, "actorEmail", principal.Email, "actorService", principal.Service)
    }''', 'auth.GetPrincipal(ctx)')
    # Controllers use local API handlers, rather than authenticated inbound RPC.
    # Wrap only their constructors with a freshly verified projected SA context.
    # The public NewAPIServerHandler remains unchanged and fail-closed.
    builder = source / "go/api/handler/builder.go"
    replace(builder, '"fmt"\n', '"fmt"\n\t"github.com/michelangelo-ai/michelangelo/go/auth"\n', '"github.com/michelangelo-ai/michelangelo/go/auth"')
    text = builder.read_text()
    for signature in ('func NewCtrlManagerHandler(', 'func (f *apiHandlerFactory) GetAPIHandler('):
        start = text.index(signature)
        end = text.index('\n}\n', start)
        section = text[start:end]
        if 'auth.WrapControllerHandler(builder.Build())' not in section:
            if section.count('return builder.Build()') != 1:
                raise RuntimeError('Controller handler constructor layout differs from reviewed source')
            text = text[:start] + section.replace('return builder.Build()', 'return auth.WrapControllerHandler(builder.Build())') + text[end:]
    builder.write_text(text)
    # gRPC's original metadata also resides in context; never stringify it.
    for path in [source / "go/kubeproto/templates/crd_svc.tmpl", *sorted((source / "proto-go/api/v2").glob("*.pb.kubeyarpc.go"))]:
        text = path.read_text()
        old = 'logger.WithValues("request", requestJSON, "context", ctx)'
        if old in text:
            path.write_text(text.replace(old, 'logger.WithValues("request", requestJSON)'))
