package veraport

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/x509"
	"encoding/hex"
	"math/big"
	"testing"
)

const (
	controllerPrincipalGolden = "controller:5cd252fb0ce8932436faf8ccd1040981b89ee4ad6b9fe9e2a2b7e71aacb27cd3"
	controllerKeyIDGolden = "5cd252fb0ce8932436faf8ccd1040981b89ee4ad6b9fe9e2a2b7e71aacb27cd3"
	workstationPrincipalGolden = "workstation:dc0ce633dbcc913dafafa4b89ac44d8ce683fdfc3f60c8bdf21213b9f2b534ba"
	workstationKeyIDGolden = "dc0ce633dbcc913dafafa4b89ac44d8ce683fdfc3f60c8bdf21213b9f2b534ba"
	challengeGolden = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
	nonceGolden = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8"
	clientSignatureGolden = "ELYNf4akjY_0T3twmOPRYGXa0hDtQ5aTrbkZN4q-gizZKTFom9fF6zvUKDIByf_EsiMB0FfgU3Va7fQRBV2O-A"
	serverSignatureGolden = "lwZlM70vHZ58krdhYp5p9eAcKI3P4GsJtjoMpZd0WJ0BQBnfu-_Pc_2RYXdLcdV3RDDUGedwf6J0H81q6WzqTQ"
	clientPayloadHexGolden = "766572616d6573682d76657261706f72742d76312f73657373696f6e2d636c69656e742d617574680a70726f746f636f6c5f76657273696f6e3d31313a76657261706f72742d76310a636f6e74726f6c6c65725f7072696e636970616c3d37353a636f6e74726f6c6c65723a356364323532666230636538393332343336666166386363643130343039383162383965653461643662396665396532613262376537316161636232376364330a636f6e74726f6c6c65725f6b65795f69643d36343a356364323532666230636538393332343336666166386363643130343039383162383965653461643662396665396532613262376537316161636232376364330a776f726b73746174696f6e5f7072696e636970616c3d37363a776f726b73746174696f6e3a646330636536333364626363393133646166616661346238396163343464386365363833666466633366363063386264663231323133623966326235333462610a7365727665725f6368616c6c656e67653d34333a41414543417751464267634943516f4c4441304f4478415245684d554652595847426b61477877644868380a636c69656e745f6e6f6e63653d34333a494345694979516c4a69636f4b536f724c4330754c7a41784d6a4d304e5459334f446b364f7a7739506a380a7265717565737465645f6361706162696c69746965733d32303a66732e726561642c70726f636573732e657865630a"
	serverPayloadHexGolden = "766572616d6573682d76657261706f72742d76312f73657373696f6e2d7365727665722d6163636570740a70726f746f636f6c5f76657273696f6e3d31313a76657261706f72742d76310a73657373696f6e5f69643d31333a73657373696f6e2d66697865640a636f6e74726f6c6c65725f7072696e636970616c3d37353a636f6e74726f6c6c65723a356364323532666230636538393332343336666166386363643130343039383162383965653461643662396665396532613262376537316161636232376364330a776f726b73746174696f6e5f7072696e636970616c3d37363a776f726b73746174696f6e3a646330636536333364626363393133646166616661346238396163343464386365363833666466633366363063386264663231323133623966326235333462610a776f726b73746174696f6e5f6b65795f69643d36343a646330636536333364626363393133646166616661346238396163343464386365363833666466633366363063386264663231323133623966326235333462610a7365727665725f6368616c6c656e67653d34333a41414543417751464267634943516f4c4441304f4478415245684d554652595847426b61477877644868380a636c69656e745f6e6f6e63653d34333a494345694979516c4a69636f4b536f724c4330754c7a41784d6a4d304e5459334f446b364f7a7739506a380a6772616e7465645f6361706162696c69746965733d32303a66732e726561642c70726f636573732e657865630a657870697265735f61745f6d733d31333a323030303030303030303030300a"
)

func fixedPrivate(d int64) *ecdsa.PrivateKey {
	curve := elliptic.P256()
	D := big.NewInt(d)
	x, y := curve.ScalarBaseMult(D.Bytes())
	return &ecdsa.PrivateKey{
		PublicKey: ecdsa.PublicKey{Curve: curve, X: x, Y: y},
		D: D,
	}
}

func TestPythonGoldenPrincipalAndSignatureCompatibility(t *testing.T) {
	controller := fixedPrivate(1)
	workstation := fixedPrivate(2)

	cp, err := PrincipalID(&controller.PublicKey, "controller")
	if err != nil { t.Fatal(err) }
	cid, err := KeyID(&controller.PublicKey)
	if err != nil { t.Fatal(err) }
	wp, err := PrincipalID(&workstation.PublicKey, "workstation")
	if err != nil { t.Fatal(err) }
	wid, err := KeyID(&workstation.PublicKey)
	if err != nil { t.Fatal(err) }

	if cp != controllerPrincipalGolden || cid != controllerKeyIDGolden {
		t.Fatalf("controller identity mismatch: %s %s", cp, cid)
	}
	if wp != workstationPrincipalGolden || wid != workstationKeyIDGolden {
		t.Fatalf("workstation identity mismatch: %s %s", wp, wid)
	}

	auth := &ClientAuth{
		FrameType: "client_auth",
		ProtocolVersion: ProtocolVersion,
		ControllerPrincipal: cp,
		ControllerKeyID: cid,
		WorkstationPrincipal: wp,
		ServerChallenge: challengeGolden,
		ClientNonce: nonceGolden,
		RequestedCapabilities: []string{"process.exec", "fs.read", "fs.read"},
		Signature: clientSignatureGolden,
	}
	if got := hex.EncodeToString(auth.SignatureBase()); got != clientPayloadHexGolden {
		t.Fatalf("client canonical bytes differ\n got: %s\nwant: %s", got, clientPayloadHexGolden)
	}
	if err := verifyP1363(&controller.PublicKey, auth.Signature, auth.SignatureBase()); err != nil {
		t.Fatalf("Go rejected Python client signature: %v", err)
	}

	accept := ServerAccept{
		FrameType: "server_accept",
		ProtocolVersion: ProtocolVersion,
		SessionID: "session-fixed",
		ControllerPrincipal: cp,
		WorkstationPrincipal: wp,
		WorkstationKeyID: wid,
		ServerChallenge: challengeGolden,
		ClientNonce: nonceGolden,
		GrantedCapabilities: []string{"process.exec", "fs.read"},
		ExpiresAtMS: 2_000_000_000_000,
		Signature: serverSignatureGolden,
	}
	if got := hex.EncodeToString(accept.SignatureBase()); got != serverPayloadHexGolden {
		t.Fatalf("server canonical bytes differ\n got: %s\nwant: %s", got, serverPayloadHexGolden)
	}
	challenge := ServerChallenge{
		FrameType: "server_challenge",
		ProtocolVersion: ProtocolVersion,
		WorkstationPrincipal: wp,
		WorkstationKeyID: wid,
		Challenge: challengeGolden,
	}
	binding, err := VerifyServerAccept(
		challenge,
		auth,
		accept,
		&workstation.PublicKey,
		1_000_000_000_000,
	)
	if err != nil {
		t.Fatalf("Go rejected Python server signature: %v", err)
	}
	if binding.SessionID != "session-fixed" {
		t.Fatalf("session binding mismatch: %#v", binding)
	}
}

func TestCreateClientAuthUsesP1363AndSortedCapabilities(t *testing.T) {
	controller := fixedPrivate(1)
	workstation := fixedPrivate(2)
	wp, _ := PrincipalID(&workstation.PublicKey, "workstation")
	wid, _ := KeyID(&workstation.PublicKey)
	challenge := ServerChallenge{
		FrameType: "server_challenge",
		ProtocolVersion: ProtocolVersion,
		WorkstationPrincipal: wp,
		WorkstationKeyID: wid,
		Challenge: challengeGolden,
	}
	nonce, _ := rawURL.DecodeString(nonceGolden)
	auth, err := CreateClientAuth(
		controller,
		challenge,
		[]string{"process.exec", "fs.read", "fs.read"},
		nonce,
	)
	if err != nil { t.Fatal(err) }
	if got := capabilityString(auth.RequestedCapabilities); got != "fs.read,process.exec" {
		t.Fatalf("capability normalization mismatch: %q", got)
	}
	if err := verifyP1363(&controller.PublicKey, auth.Signature, auth.SignatureBase()); err != nil {
		t.Fatal(err)
	}
	raw, err := rawURL.DecodeString(auth.Signature)
	if err != nil || len(raw) != 64 {
		t.Fatalf("signature is not 64-byte P1363: len=%d err=%v", len(raw), err)
	}
}

func TestPinnedWorkstationAndGrantedCapabilityFailClosed(t *testing.T) {
	controller := fixedPrivate(1)
	workstation := fixedPrivate(2)
	other := fixedPrivate(3)
	cp, _ := PrincipalID(&controller.PublicKey, "controller")
	cid, _ := KeyID(&controller.PublicKey)
	wp, _ := PrincipalID(&workstation.PublicKey, "workstation")
	wid, _ := KeyID(&workstation.PublicKey)
	auth := &ClientAuth{
		FrameType: "client_auth",
		ProtocolVersion: ProtocolVersion,
		ControllerPrincipal: cp,
		ControllerKeyID: cid,
		WorkstationPrincipal: wp,
		ServerChallenge: challengeGolden,
		ClientNonce: nonceGolden,
		RequestedCapabilities: []string{"fs.read"},
		Signature: clientSignatureGolden,
	}
	challenge := ServerChallenge{
		FrameType: "server_challenge",
		ProtocolVersion: ProtocolVersion,
		WorkstationPrincipal: wp,
		WorkstationKeyID: wid,
		Challenge: challengeGolden,
	}
	accept := ServerAccept{
		FrameType: "server_accept",
		ProtocolVersion: ProtocolVersion,
		SessionID: "x",
		ControllerPrincipal: cp,
		WorkstationPrincipal: wp,
		WorkstationKeyID: wid,
		ServerChallenge: challengeGolden,
		ClientNonce: nonceGolden,
		GrantedCapabilities: []string{"fs.write"},
		ExpiresAtMS: 2_000_000_000_000,
		Signature: serverSignatureGolden,
	}
	if _, err := VerifyServerAccept(challenge, auth, accept, &other.PublicKey, 1); err == nil {
		t.Fatal("wrong pinned workstation key accepted")
	}
	if _, err := VerifyServerAccept(challenge, auth, accept, &workstation.PublicKey, 1); err == nil {
		t.Fatal("unrequested granted capability accepted")
	}
}

func TestSPKIGoldenEncodingMatchesPython(t *testing.T) {
	controller := fixedPrivate(1)
	spki, err := x509.MarshalPKIXPublicKey(&controller.PublicKey)
	if err != nil { t.Fatal(err) }
	want, _ := hex.DecodeString("3059301306072a8648ce3d020106082a8648ce3d030107034200046b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c2964fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5")
	if !bytes.Equal(spki, want) {
		t.Fatalf("Go SPKI differs from Python: %x", spki)
	}
}
