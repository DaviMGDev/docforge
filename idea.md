the idea here is to make a converter pdf->md using llms(no ocr).

```pseudo 
partition(file.pdf) -> []pdf {
    divide file.pdf by 8, must len(chunk.pdf) <= 8
}
convert(chunk.pdf) -> []img {
    each page will be an image
}

convert(chunk.pdf) -> txt {
    not exactly ocr nor llm based, will be simple raw text extraction of pdfs.
}

convert(chunk.pdf) -> []json {
    raw_text = convert(chunk.pdf)->txt 
    images = convert(chunk.pdf)->[]img 

    identify:
       1. tables
       2. math symbols
       3. headers and subheaders
       4. codeblocks 
       5. embedded images 
       6. simple content 
       7. etc 
       
    math symbols shall be converted to latex
    embedded images shall be extracted, placed in an assets and refereced in the json.
}

convert(material.json) -> file.md {
    if the json is well structured enough, it is very simple to simply transform each structure into its markdown equivalent
}

convert(file.pdf) -> file.md {
    partitionate the file, for each chunk convert into json, at the end merge the json and spit out the convert(.json)->md
}
```


python ideally. uv for package management. good looking logging and cli please. llm calling can happen 3 at the same time, the other conversions that are deterministic shall respect the hardware constraints. 
