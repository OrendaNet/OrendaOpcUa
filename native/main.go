// The collector has no listening port. Its parent exchanges bounded RPCs over stdio.
package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/gopcua/opcua"
	"github.com/gopcua/opcua/ua"
)

type object = map[string]any
type request struct {
	Seq                                                            int
	Op, URL, Fingerprint, Username, Password, Cert, Key, URI, Node string
	Offset                                                         int
	Nodes                                                          []string
}
type collector struct {
	client     *opcua.Client
	sub        *opcua.Subscription
	events     chan *opcua.PublishNotificationData
	refreshing bool
}

func identity() (object, error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, err
	}
	n, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, err
	}
	uri := &url.URL{Scheme: "urn", Opaque: "orendabox:opcua:" + n.Text(16)}
	host, _ := os.Hostname()
	cert := x509.Certificate{SerialNumber: n, Subject: pkix.Name{CommonName: "OrendaBox OPC UA Integrator"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().AddDate(5, 0, 0), URIs: []*url.URL{uri}, DNSNames: []string{host}, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment | x509.KeyUsageDataEncipherment | x509.KeyUsageContentCommitment, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, &cert, &cert, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	priv, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, err
	}
	return object{"uri": uri.String(), "cert": string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})), "key": string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: priv}))}, nil
}

func discover(ctx context.Context, address string) (*ua.EndpointDescription, object, error) {
	u, err := url.Parse(address)
	if err != nil || u.Scheme != "opc.tcp" || u.Hostname() == "" || u.Port() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || len(address) > 768 {
		return nil, nil, errors.New("Enter an opc.tcp:// controller address and port")
	}
	eps, err := opcua.GetEndpoints(ctx, address, opcua.DialTimeout(5*time.Second), opcua.RequestTimeout(5*time.Second))
	if err != nil {
		return nil, nil, err
	}
	for _, policy := range []string{"Aes256_Sha256_RsaPss", "Aes128_Sha256_RsaOaep", "Basic256Sha256"} {
		for _, ep := range eps {
			if ep.SecurityMode != ua.MessageSecurityModeSignAndEncrypt || !strings.HasSuffix(ep.SecurityPolicyURI, "#"+policy) {
				continue
			}
			certs, err := x509.ParseCertificates(ep.ServerCertificate)
			if err != nil || len(certs) == 0 {
				return nil, nil, errors.New("Invalid controller certificate")
			}
			cert := certs[0]
			if time.Now().Before(cert.NotBefore) || time.Now().After(cert.NotAfter) {
				return nil, nil, errors.New("Controller certificate is outside its validity period")
			}
			hash := sha256.Sum256(cert.Raw)
			types := []string{}
			for _, t := range ep.UserIdentityTokens {
				if t.TokenType == ua.UserTokenTypeAnonymous {
					types = append(types, "Anonymous")
				}
				if t.TokenType == ua.UserTokenTypeUserName {
					types = append(types, "UserName")
				}
			}
			return ep, object{"policy": policy, "fingerprint": hex.EncodeToString(hash[:]), "name": ep.Server.ApplicationName.Text, "uri": ep.Server.ApplicationURI, "expires": cert.NotAfter.UTC(), "identities": types}, nil
		}
	}
	return nil, nil, errors.New("Enable a supported Sign & Encrypt endpoint on the controller")
}
func (s *collector) close(ctx context.Context) {
	if s.client != nil {
		s.client.Close(ctx)
	}
	s.client = nil
	s.sub = nil
	s.events = nil
	s.refreshing = false
}
func (s *collector) connect(ctx context.Context, r request) (any, error) {
	ep, info, err := discover(ctx, r.URL)
	if err != nil {
		return nil, err
	}
	if info["fingerprint"] != r.Fingerprint {
		return nil, errors.New("Controller certificate changed. Compare its fingerprint in Connection")
	}
	cert, _ := pem.Decode([]byte(r.Cert))
	key, _ := pem.Decode([]byte(r.Key))
	if cert == nil || key == nil {
		return nil, errors.New("Invalid saved client identity")
	}
	k, err := x509.ParsePKCS8PrivateKey(key.Bytes)
	if err != nil {
		return nil, err
	}
	pk, ok := k.(*rsa.PrivateKey)
	if !ok {
		return nil, errors.New("Invalid client private key")
	}
	auth := opcua.AuthAnonymous()
	typ := ua.UserTokenTypeAnonymous
	if r.Username != "" {
		auth = opcua.AuthUsername(r.Username, r.Password)
		typ = ua.UserTokenTypeUserName
	}
	offered := false
	for _, token := range ep.UserIdentityTokens {
		if token.TokenType == typ {
			offered = true
			break
		}
	}
	if !offered {
		return nil, errors.New("Controller does not offer that sign-in method")
	}
	c, err := opcua.NewClient(r.URL, opcua.AutoReconnect(false), opcua.DialTimeout(5*time.Second), opcua.RequestTimeout(6*time.Second), opcua.Certificate(cert.Bytes), opcua.PrivateKey(pk), opcua.ApplicationName("OrendaBox OPC UA Integrator"), opcua.ApplicationURI(r.URI), auth, opcua.SecurityFromEndpoint(ep, typ))
	if err != nil {
		return nil, err
	}
	if err = c.Connect(ctx); err != nil {
		c.Close(ctx)
		return nil, err
	}
	if err = c.UpdateNamespaces(ctx); err != nil {
		c.Close(ctx)
		return nil, err
	}
	s.close(ctx)
	s.client = c
	return object{"namespaces": c.Namespaces()}, nil
}
func (s *collector) run(ctx context.Context, r request) (any, error) {
	switch r.Op {
	case "identity":
		return identity()
	case "discover":
		_, info, err := discover(ctx, r.URL)
		return info, err
	case "connect":
		return s.connect(ctx, r)
	case "close":
		s.close(ctx)
		return true, nil
	}
	if s.client == nil {
		return nil, errors.New("Connect the controller first")
	}
	switch r.Op {
	case "read":
		return s.read(ctx, r.Nodes)
	case "browse":
		return s.browse(ctx, r.Node, r.Offset)
	case "preview":
		return s.preview(ctx, r.Node)
	case "notifier":
		return s.notifier(ctx, r.Node)
	}
	return nil, errors.New("Unknown native operation")
}
func main() {
	var s collector
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), 256*1024)
	out := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		var r request
		if json.Unmarshal(scanner.Bytes(), &r) != nil {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		result, err := s.run(ctx, r)
		cancel()
		reply := object{"seq": r.Seq, "result": result}
		if err != nil {
			reply["error"] = fmt.Sprint(err)
		}
		if out.Encode(reply) != nil {
			return
		}
	}
}
