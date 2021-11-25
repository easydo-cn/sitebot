"""
/*
 * Copyright (c) 2019 EasyDo, Inc. <panjunyong@easydo.cn>
 *
 * This program is free software: you can use, redistribute, and/or modify
 * it under the terms of the GNU Affero General Public License, version 3
 * or later ("AGPL"), as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */
"""
# -*- coding: utf-8 -*-
"""
>>> from Crypto.Signature import PKCS1_v1_5
>>> from Crypto.Hash import SHA
>>> from Crypto.PublicKey import RSA
>>>
>>> message = 'To be signed'
>>> key = RSA.importKey(open('privkey.der').read())
>>> h = SHA.new(message)
>>> signer = PKCS1_v1_5.new(key)
>>> signature = signer.sign(h)


>>> key = RSA.importKey(open('pubkey.der').read())
>>> h = SHA.new(message)
>>> verifier = PKCS1_v1_5.new(key)
>>> if verifier.verify(h, signature):
>>>    print "The signature is authentic."
>>> else:
>>>    print "The signature is not authentic."

"""
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5


def as_bytes(s):
    if isinstance(s, unicode):
        return s.encode('utf-8')
    return s


def sign(private_key, text):
    """sign the text with private key"""
    key = RSA.importKey(private_key)
    _hash = SHA256.new(as_bytes(text))
    signer = PKCS1_v1_5.new(key)
    return signer.sign(_hash)


def verify(public_key, text, signature):
    """verify the signature"""
    key = RSA.importKey(public_key)
    _hash = SHA256.new(as_bytes(text))
    verifier = PKCS1_v1_5.new(key)
    return verifier.verify(_hash, signature)

if __name__ == '__main__':

    from Crypto import Random

    random_generator = Random.new().read

    key = RSA.generate(1024, random_generator)

    print 'can sign: ', key.can_sign()
    print 'has private: ', key.has_private()
    print 'can encrypt: ', key.can_encrypt()

    print 'public key: ', key.publickey().exportKey('PEM')
    print 'private key: ', key.exportKey('PEM')

    text = 'this message send from bob.'
    public_key = key.publickey().exportKey('PEM')
    private_key = key.exportKey('PEM')
    signature = sign(private_key, text)
    assert verify(public_key, text, signature) is True
