/**
 * OpenSSL-compatible AES-256-CBC encryption/decryption using Web Crypto API.
 *
 * File format (same as `openssl enc -aes-256-cbc -salt -pbkdf2`):
 *   Bytes  0-7:  "Salted__"  (magic header)
 *   Bytes  8-15: 8-byte random salt
 *   Bytes 16+:   AES-256-CBC ciphertext (PKCS7 padded)
 *
 * Key derivation:
 *   PBKDF2-HMAC-SHA256(password, salt, iterations, dkLen=48)
 *   -> first 32 bytes = AES key, last 16 bytes = IV
 */

window.ICS = window.ICS || {};

var MAGIC = new TextEncoder().encode("Salted__");

function _checkWebCrypto() {
  if (!window.crypto || !window.crypto.subtle) {
    throw new Error(
      "Web Crypto API is not available. Please access this page via HTTPS (GitHub Pages) or http://localhost. " +
      "Current protocol: " + location.protocol + " host: " + location.host
    );
  }
}

async function _deriveKeyAndIV(password, salt, iterations) {
  _checkWebCrypto();
  var enc = new TextEncoder();
  var baseKey = await window.crypto.subtle.importKey(
    "raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]
  );
  var bits = await window.crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: salt, iterations: iterations, hash: "SHA-256" },
    baseKey, 48 * 8
  );
  var key = await window.crypto.subtle.importKey(
    "raw", bits.slice(0, 32), { name: "AES-CBC" }, false, ["encrypt", "decrypt"]
  );
  return { key: key, iv: new Uint8Array(bits.slice(32, 48)) };
}

async function _icsDecrypt(encryptedBytes, password, iterations) {
  iterations = iterations || 10000;
  var headerStr = new TextDecoder().decode(encryptedBytes.slice(0, 8));
  if (headerStr !== "Salted__") {
    throw new Error("Invalid file: missing OpenSSL 'Salted__' header");
  }
  var salt = encryptedBytes.slice(8, 16);
  var ciphertext = encryptedBytes.slice(16);
  var derived = await _deriveKeyAndIV(password, salt, iterations);
  var plainBuffer = await window.crypto.subtle.decrypt(
    { name: "AES-CBC", iv: derived.iv }, derived.key, ciphertext
  );
  return new Uint8Array(plainBuffer);
}

async function _icsEncrypt(plainBytes, password, iterations) {
  iterations = iterations || 10000;
  _checkWebCrypto();
  var salt = window.crypto.getRandomValues(new Uint8Array(8));
  var derived = await _deriveKeyAndIV(password, salt, iterations);
  var cipherBuffer = await window.crypto.subtle.encrypt(
    { name: "AES-CBC", iv: derived.iv }, derived.key, plainBytes
  );
  var cipherBytes = new Uint8Array(cipherBuffer);
  var result = new Uint8Array(MAGIC.length + salt.length + cipherBytes.length);
  result.set(MAGIC, 0);
  result.set(salt, MAGIC.length);
  result.set(cipherBytes, MAGIC.length + salt.length);
  return result;
}

function _icsBuildPassword(secrets) {
  return secrets.stuid + secrets.uispsw + secrets.dashscope + secrets.smtp;
}

window.ICS.crypto = {
  decrypt: _icsDecrypt,
  encrypt: _icsEncrypt,
  buildPassword: _icsBuildPassword,
};
