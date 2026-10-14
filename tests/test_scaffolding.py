import docforge
import pdf2md


def test_version():
    assert docforge.__version__ == "0.1.0"
    assert pdf2md.__version__ == docforge.__version__


def test_alias_compatibility():
    import pdf2md.models.ast as legacy_ast
    import docforge.models.ast as new_ast
    assert legacy_ast.ParagraphBlock is new_ast.ParagraphBlock
