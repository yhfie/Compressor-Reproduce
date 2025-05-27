git clone https://github.com/tree-sitter/tree-sitter-go
git clone https://github.com/tree-sitter/tree-sitter-javascript
git clone https://github.com/tree-sitter/tree-sitter-python
git clone https://github.com/tree-sitter/tree-sitter-ruby
git clone https://github.com/tree-sitter/tree-sitter-php
git clone https://github.com/tree-sitter/tree-sitter-java
git clone https://github.com/tree-sitter/tree-sitter-c-sharp
git clone https://github.com/tree-sitter/tree-sitter-c

(cd tree-sitter-go && git checkout bbaa67a180cfe0c943e50c55130918be8efb20bd)
(cd tree-sitter-javascript && git checkout fdeb68ac8d2bd5a78b943528bb68ceda3aade2eb)
(cd tree-sitter-python && git checkout 2b9e9e0d231d5dd9f491d47f704817baee7d5af0)
(cd tree-sitter-php && git checkout 0a99deca13c4af1fb9adcb03c958bfc9f4c740a9)
(cd tree-sitter-java && git checkout ac14b4b1884102839455d32543ab6d53ae089ab7)
(cd tree-sitter-ruby && git checkout 7a010836b74351855148818d5cb8170dc4df8e6a)
(cd tree-sitter-c-sharp && git checkout 7a47daeaf0d410dd1a91c97b274bb7276dd96605)
(cd tree-sitter-c && git checkout ad095896dd223f1c22b85ac5ec84ab11fb732b07)

python3 build.py
