// Monaco용 Solidity 언어 등록 (Monarch 문법 + 테마).
// 별도 패키지 없이 경량 등록. 한 번만 실행되도록 가드.

let registered = false

export function registerSolidity(monaco: any) {
  if (registered) return
  registered = true

  monaco.languages.register({ id: 'solidity' })

  monaco.languages.setLanguageConfiguration('solidity', {
    comments: { lineComment: '//', blockComment: ['/*', '*/'] },
    brackets: [
      ['{', '}'],
      ['[', ']'],
      ['(', ')'],
    ],
    autoClosingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '"', close: '"' },
      { open: "'", close: "'" },
    ],
  })

  monaco.languages.setMonarchTokensProvider('solidity', {
    defaultToken: '',
    keywords: [
      'pragma', 'solidity', 'import', 'as', 'from', 'contract', 'interface', 'library',
      'abstract', 'is', 'using', 'function', 'modifier', 'event', 'error', 'struct',
      'enum', 'mapping', 'constructor', 'fallback', 'receive', 'public', 'private',
      'internal', 'external', 'view', 'pure', 'payable', 'nonpayable', 'virtual',
      'override', 'returns', 'return', 'memory', 'storage', 'calldata', 'constant',
      'immutable', 'indexed', 'anonymous', 'new', 'delete', 'emit', 'if', 'else',
      'for', 'while', 'do', 'break', 'continue', 'try', 'catch', 'require', 'assert',
      'revert', 'unchecked', 'assembly', 'type', 'this', 'super',
    ],
    typeKeywords: [
      'address', 'bool', 'string', 'bytes', 'byte', 'int', 'uint', 'fixed', 'ufixed',
    ],
    literals: [
      'true', 'false', 'wei', 'gwei', 'ether', 'seconds', 'minutes', 'hours', 'days',
      'weeks', 'years',
    ],
    builtins: [
      'msg', 'block', 'tx', 'now', 'abi', 'keccak256', 'sha256', 'ecrecover',
      'selfdestruct', 'addmod', 'mulmod', 'gasleft', 'blockhash',
    ],
    operators: [
      '=', '>', '<', '!', '~', '?', ':', '==', '<=', '>=', '!=', '&&', '||', '++',
      '--', '+', '-', '*', '/', '&', '|', '^', '%', '<<', '>>', '+=', '-=', '*=',
      '/=', '&=', '|=', '^=', '=>',
    ],
    symbols: /[=><!~?:&|+\-*/^%]+/,
    tokenizer: {
      root: [
        // IQDebug 어노테이션 주석 (// @During, // @StateVar ...) 특별 강조
        [/\/\/\s*@\w+.*$/, 'annotation'],
        // 일반 주석
        [/\/\/.*$/, 'comment'],
        [/\/\*/, 'comment', '@blockComment'],

        // 타입: uint256 / int8 / bytes32 등 (접미 숫자 포함)
        [/\b(u?int|bytes)([0-9]*)\b/, 'type'],

        // 식별자/키워드
        [
          /[a-zA-Z_$][\w$]*/,
          {
            cases: {
              '@keywords': 'keyword',
              '@typeKeywords': 'type',
              '@literals': 'constant',
              '@builtins': 'predefined',
              '@default': 'identifier',
            },
          },
        ],

        // 숫자
        [/0[xX][0-9a-fA-F]+/, 'number.hex'],
        [/\d+(\.\d+)?([eE][+-]?\d+)?/, 'number'],

        // 문자열
        [/"([^"\\]|\\.)*$/, 'string.invalid'],
        [/'([^'\\]|\\.)*$/, 'string.invalid'],
        [/"/, 'string', '@stringDouble'],
        [/'/, 'string', '@stringSingle'],

        [/@symbols/, { cases: { '@operators': 'operator', '@default': '' } }],
        [/[{}()[\]]/, '@brackets'],
        [/[;,.]/, 'delimiter'],
      ],
      blockComment: [
        [/[^/*]+/, 'comment'],
        [/\*\//, 'comment', '@pop'],
        [/[/*]/, 'comment'],
      ],
      stringDouble: [
        [/[^"\\]+/, 'string'],
        [/\\./, 'string.escape'],
        [/"/, 'string', '@pop'],
      ],
      stringSingle: [
        [/[^'\\]+/, 'string'],
        [/\\./, 'string.escape'],
        [/'/, 'string', '@pop'],
      ],
    },
  })

  // vs-dark 기반 + annotation 토큰 색 추가
  monaco.editor.defineTheme('iqdebug-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'annotation', foreground: 'c586c0', fontStyle: 'italic' },
      { token: 'type', foreground: '4ec9b0' },
      { token: 'predefined', foreground: 'dcdcaa' },
      { token: 'constant', foreground: '569cd6' },
    ],
    colors: {},
  })
}
