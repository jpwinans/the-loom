"""Symbolic math via sympy imported natively.

sympy is imported directly and the seven CLI operations run in-process — a
parse fallback (LaTeX -> transformed sympify -> raw sympify), result
formatting, a 5-second SIGALRM timeout, and a ``{success, error}`` envelope.

parse_latex requires antlr4, which this environment does not have, so the LaTeX
branch always falls through — LaTeX-only inputs fail with a SympifyError.
"""

from __future__ import annotations

import signal
from typing import Any

Doc = dict[str, Any]

DEFAULT_TIMEOUT = 5


def _parse_expr(expr_str: str) -> Any:
    import sympy
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        standard_transformations,
    )
    from sympy.parsing.sympy_parser import (
        parse_expr as sympy_parse,
    )

    transformations = standard_transformations + (
        implicit_multiplication_application,
        convert_xor,
    )
    try:
        from sympy.parsing.latex import parse_latex

        result = parse_latex(expr_str)
        if result is not None:
            return result
    except Exception:
        pass
    try:
        return sympy_parse(expr_str, transformations=transformations)
    except Exception:
        pass
    return sympy.sympify(expr_str)


def _parse_equation(eq_str: str) -> Any:
    import sympy

    eq_str = eq_str.strip()
    if "=" in eq_str and "==" not in eq_str and "<=" not in eq_str and ">=" not in eq_str:
        parts = eq_str.split("=", 1)
        return sympy.Eq(_parse_expr(parts[0].strip()), _parse_expr(parts[1].strip()))
    return _parse_expr(eq_str)


def _to_latex(expr: Any) -> str:
    import sympy

    try:
        return str(sympy.latex(expr))
    except Exception:
        return str(expr)


def _result_to_str(result: Any) -> str:
    if isinstance(result, list):
        return str([str(r) for r in result])
    if isinstance(result, dict):
        return str({str(k): str(v) for k, v in result.items()})
    return str(result)


# =============================================================================
# The seven CLI operations
# =============================================================================


def op_solve(params: Doc) -> Doc:
    import sympy

    equations = params.get("equations") or [params.get("equation", "")]
    if isinstance(equations, str):
        equations = [equations]
    variables = params.get("variables") or params.get("variable", "x")
    if isinstance(variables, str):
        variables = [variables]

    parsed_eqs = [_parse_equation(eq) for eq in equations]
    parsed_vars = [sympy.Symbol(v.strip()) for v in variables]

    if len(parsed_eqs) == 1 and len(parsed_vars) == 1:
        solutions = sympy.solve(parsed_eqs[0], parsed_vars[0])
    else:
        solutions = sympy.solve(parsed_eqs, parsed_vars)

    return {
        "success": True,
        "result": _result_to_str(solutions),
        "latex_result": (
            _to_latex(solutions)
            if not isinstance(solutions, list)
            else ", ".join(_to_latex(s) for s in solutions)
        ),
        "solution_count": len(solutions) if isinstance(solutions, list) else 1,
    }


def op_simplify(params: Doc) -> Doc:
    import sympy

    simplified = sympy.simplify(_parse_expr(params["expression"]))
    return {"success": True, "result": str(simplified), "latex_result": _to_latex(simplified)}


def op_verify(params: Doc) -> Doc:
    """Verify a mathematical claim. Three modes (checked in this order):

    1. Predicate check: sub_operation + expression (+ optional args)
       e.g. {"sub_operation": "divisible_by", "expression": "256", "args": [8]}
       e.g. {"sub_operation": "is_prime", "expression": "17"}
       Supported sub_operations: divisible_by, is_prime, is_integer,
         is_even, is_odd, is_positive, is_negative, is_zero

    2. Equality check: lhs/rhs (with optional substitutions)
       e.g. {"lhs": "36/12", "rhs": "21/7"}
       e.g. {"lhs": "10*t", "rhs": "3*s + g", "substitutions": {"t": "g", "s": "3*g"}}

    3. Legacy root check: equation + variable + value (the CLI-exposed mode)
       e.g. {"equation": "x**2 - 4 = 0", "variable": "x", "value": "2"}
    """
    import sympy

    sub_op = params.get("sub_operation")

    # Mode 2: predicate check
    if sub_op:
        expr = _parse_expr(str(params.get("expression", "0")))
        subs = params.get("substitutions") or {}
        if subs:
            sub_map = {sympy.Symbol(k): _parse_expr(str(v)) for k, v in subs.items()}
            expr = expr.subs(sub_map)
        expr = sympy.simplify(expr)

        args = params.get("args") or []

        if sub_op == "divisible_by":
            divisor = _parse_expr(str(args[0])) if args else None
            if divisor is None:
                return {"success": False, "error": "divisible_by requires args[0] = divisor"}
            try:
                is_valid = (expr % divisor) == 0
            except Exception:
                is_valid = sympy.simplify(expr / divisor).is_integer
            return {
                "success": True,
                "is_valid": bool(is_valid),
                "expression_value": str(expr),
                "latex_result": _to_latex(expr),
            }

        if sub_op == "is_prime":
            try:
                is_valid = sympy.isprime(int(expr))
            except Exception:
                is_valid = False
            return {
                "success": True,
                "is_valid": bool(is_valid),
                "expression_value": str(expr),
                "latex_result": _to_latex(expr),
            }

        predicate_map = {
            "is_integer": lambda e: e.is_integer,
            "is_even": lambda e: e.is_even,
            "is_odd": lambda e: e.is_odd,
            "is_positive": lambda e: e.is_positive,
            "is_negative": lambda e: e.is_negative,
            "is_zero": lambda e: e.is_zero,
        }
        if sub_op in predicate_map:
            result = predicate_map[sub_op](expr)
            # SymPy returns None for undetermined
            is_valid = bool(result) if result is not None else False
            return {
                "success": True,
                "is_valid": is_valid,
                "expression_value": str(expr),
                "latex_result": _to_latex(expr),
            }

        return {"success": False, "error": f"Unknown verify sub_operation: {sub_op}"}

    # Mode 1: equality check (general lhs/rhs form)
    lhs_str = params.get("lhs")
    rhs_str = params.get("rhs")
    if lhs_str is not None and rhs_str is not None:
        lhs = _parse_expr(str(lhs_str))
        rhs = _parse_expr(str(rhs_str))
        subs = params.get("substitutions") or {}
        if subs:
            sub_map = {sympy.Symbol(k): _parse_expr(str(v)) for k, v in subs.items()}
            lhs = lhs.subs(sub_map)
            rhs = rhs.subs(sub_map)
        diff = sympy.simplify(lhs - rhs)
        is_valid = diff == 0 or diff is sympy.true or diff == sympy.S.true
        return {
            "success": True,
            "is_valid": bool(is_valid),
            "lhs_value": str(sympy.simplify(lhs)),
            "rhs_value": str(sympy.simplify(rhs)),
            "difference": str(diff),
            "latex_result": _to_latex(diff),
        }

    # Mode 3: legacy root check (equation + variable + value).
    # The CLI schema exposes only this mode.
    equation = _parse_equation(params["equation"])
    variable = sympy.Symbol(params["variable"])
    value = _parse_expr(str(params["value"]))

    if isinstance(equation, sympy.Eq):
        substituted = equation.lhs.subs(variable, value) - equation.rhs.subs(variable, value)
    else:
        substituted = equation.subs(variable, value)

    simplified = sympy.simplify(substituted)
    is_valid = simplified == 0 or simplified is sympy.true or simplified == sympy.S.true
    return {
        "success": True,
        "is_valid": bool(is_valid),
        "substitution_result": str(simplified),
        "latex_result": _to_latex(simplified),
    }


def op_factor(params: Doc) -> Doc:
    import sympy

    factored = sympy.factor(_parse_expr(params["expression"]))
    return {"success": True, "result": str(factored), "latex_result": _to_latex(factored)}


def op_expand(params: Doc) -> Doc:
    import sympy

    expanded = sympy.expand(_parse_expr(params["expression"]))
    return {"success": True, "result": str(expanded), "latex_result": _to_latex(expanded)}


def op_evaluate(params: Doc) -> Doc:
    expr = _parse_expr(params["expression"])
    substitutions = params.get("substitutions") or {}
    if substitutions:
        import sympy

        sub_dict = {sympy.Symbol(k): _parse_expr(str(v)) for k, v in substitutions.items()}
        expr = expr.subs(sub_dict)
    try:
        numeric = float(expr.evalf())
        return {
            "success": True,
            "result": str(numeric),
            "latex_result": _to_latex(expr.evalf()),
            "numeric_value": numeric,
        }
    except (TypeError, ValueError):
        return {
            "success": True,
            "result": str(expr.evalf()),
            "latex_result": _to_latex(expr.evalf()),
            "numeric_value": None,
            "note": "Expression could not be fully evaluated to a number",
        }


def op_latex(params: Doc) -> Doc:
    expr = _parse_expr(params["expression"])
    return {"success": True, "result": _to_latex(expr), "latex_result": _to_latex(expr)}


# =============================================================================
# Calculus
# =============================================================================


def op_integrate(params: Doc) -> Doc:
    """Integrate an expression, definite or indefinite."""
    import sympy

    expr = _parse_expr(params["expression"])
    var = sympy.Symbol(params.get("variable", "x"))

    bounds = params.get("bounds")
    if bounds and bounds.get("lower") is not None and bounds.get("upper") is not None:
        lower = _parse_expr(str(bounds["lower"]))
        upper_str = str(bounds["upper"])
        upper = (
            sympy.oo if upper_str.lower() in ("inf", "infinity", "oo") else _parse_expr(upper_str)
        )
        result = sympy.integrate(expr, (var, lower, upper))
    else:
        result = sympy.integrate(expr, var)

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


def op_diff(params: Doc) -> Doc:
    """Differentiate an expression."""
    import sympy

    expr = _parse_expr(params["expression"])
    var = sympy.Symbol(params.get("variable", "x"))
    order = int(params.get("order", 1))
    result = sympy.diff(expr, var, order)

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


def op_limit(params: Doc) -> Doc:
    """Compute a limit."""
    import sympy

    expr = _parse_expr(params["expression"])
    var = sympy.Symbol(params.get("variable", "x"))
    point_str = str(params["point"])
    if point_str.lower() in ("inf", "infinity", "oo"):
        point = sympy.oo
    elif point_str.lower() in ("-inf", "-infinity", "-oo"):
        point = -sympy.oo
    else:
        point = _parse_expr(point_str)
    direction = params.get("direction", "+-")
    result = sympy.limit(expr, var, point, direction)

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


def op_series(params: Doc) -> Doc:
    """Compute Taylor/Maclaurin series expansion."""
    import sympy

    expr = _parse_expr(params["expression"])
    var = sympy.Symbol(params.get("variable", "x"))
    point = _parse_expr(str(params.get("point", "0")))
    order = int(params.get("order", 6))
    result = sympy.series(expr, var, point, n=order)

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


# =============================================================================
# Discrete math
# =============================================================================


def op_summation(params: Doc) -> Doc:
    """Compute a finite or infinite sum."""
    import sympy

    expr = _parse_expr(params["expression"])
    var = sympy.Symbol(params.get("variable", "n"))
    bounds = params.get("bounds", {})
    lower = _parse_expr(str(bounds.get("lower", "0")))
    upper_str = str(bounds.get("upper", "oo"))
    upper = sympy.oo if upper_str.lower() in ("inf", "infinity", "oo") else _parse_expr(upper_str)
    result = sympy.summation(expr, (var, lower, upper))

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


def op_product(params: Doc) -> Doc:
    """Compute a finite or infinite product."""
    import sympy

    expr = _parse_expr(params["expression"])
    var = sympy.Symbol(params.get("variable", "n"))
    bounds = params.get("bounds", {})
    lower = _parse_expr(str(bounds.get("lower", "1")))
    upper_str = str(bounds.get("upper", "oo"))
    upper = sympy.oo if upper_str.lower() in ("inf", "infinity", "oo") else _parse_expr(upper_str)
    result = sympy.product(expr, (var, lower, upper))

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


# =============================================================================
# Specialized simplification
# =============================================================================


def op_trigsimp(params: Doc) -> Doc:
    """Simplify using trigonometric identities."""
    import sympy

    result = sympy.trigsimp(_parse_expr(params["expression"]))
    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


def op_apart(params: Doc) -> Doc:
    """Partial fraction decomposition."""
    import sympy

    expr = _parse_expr(params["expression"])
    var = sympy.Symbol(params.get("variable", "x")) if params.get("variable") else None
    result = sympy.apart(expr, var) if var else sympy.apart(expr)

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


# =============================================================================
# Linear algebra
# =============================================================================


def op_matrix(params: Doc) -> Doc:
    """Matrix ops: det, inv, eigenvals, eigenvects, rank, rref, nullspace,
    trace, transpose, charpoly."""
    import sympy

    matrix_data = params["matrix"]
    parsed = []
    for row in matrix_data:
        parsed.append([_parse_expr(str(elem)) for elem in row])
    matrix = sympy.Matrix(parsed)

    sub_op = params.get("sub_operation", "det")

    if sub_op == "det":
        result = matrix.det()
    elif sub_op == "inv":
        result = matrix.inv()
    elif sub_op == "eigenvals":
        result = matrix.eigenvals()
    elif sub_op == "eigenvects":
        result = matrix.eigenvects()
    elif sub_op == "rank":
        result = matrix.rank()
    elif sub_op == "rref":
        rref_matrix, _ = matrix.rref()
        result = rref_matrix
    elif sub_op == "nullspace":
        result = matrix.nullspace()
    elif sub_op == "trace":
        result = matrix.trace()
    elif sub_op == "transpose":
        result = matrix.T
    elif sub_op == "charpoly":
        lam = sympy.Symbol(params.get("variable", "lambda"))
        result = matrix.charpoly(lam).as_expr()
    else:
        return {"success": False, "error": f"Unknown matrix operation: {sub_op}"}

    return {"success": True, "result": _result_to_str(result), "latex_result": _to_latex(result)}


# =============================================================================
# Number theory
# =============================================================================


def op_number_theory(params: Doc) -> Doc:
    """Number theory: factorint, gcd, lcm, isprime, mod_inverse, totient,
    divisor_count, divisors, mod, divisibility."""
    import sympy
    from sympy.ntheory import divisor_count, divisors, factorint, totient

    sub_op = params.get("sub_operation", "factorint")

    if sub_op == "factorint":
        n = int(_parse_expr(str(params["expression"])))
        result = factorint(n)
    elif sub_op == "gcd":
        args = params.get("args", [])
        result = sympy.gcd(int(args[0]), int(args[1]))
    elif sub_op == "lcm":
        args = params.get("args", [])
        result = sympy.lcm(int(args[0]), int(args[1]))
    elif sub_op == "isprime":
        n = int(_parse_expr(str(params["expression"])))
        result = sympy.isprime(n)
    elif sub_op == "mod_inverse":
        args = params.get("args", [])
        result = pow(int(args[0]), -1, int(args[1]))
    elif sub_op == "totient":
        n = int(_parse_expr(str(params["expression"])))
        result = totient(n)
    elif sub_op == "divisor_count":
        n = int(_parse_expr(str(params["expression"])))
        result = divisor_count(n)
    elif sub_op == "divisors":
        n = int(_parse_expr(str(params["expression"])))
        result = list(divisors(n))
    elif sub_op == "mod":
        expr_val = _parse_expr(str(params["expression"]))
        subs = params.get("substitutions", {})
        if subs:
            expr_val = expr_val.subs(
                {sympy.Symbol(k): _parse_expr(str(v)) for k, v in subs.items()}
            )
        args = params.get("args", [])
        if args:
            modulus = int(args[0])
            result = int(expr_val) % modulus
        else:
            return {"success": False, "error": "mod requires args with modulus"}
    elif sub_op == "divisibility":
        args = params.get("args", [])
        if len(args) >= 2 and isinstance(args[1], list):
            divisor = int(args[0])
            numbers = [int(x) for x in args[1]]
            result = [num for num in numbers if num % divisor == 0]
        elif args:
            divisor = int(args[0])
            expr_val = _parse_expr(str(params["expression"]))
            subs = params.get("substitutions", {})
            if subs:
                expr_val = expr_val.subs(
                    {sympy.Symbol(k): _parse_expr(str(v)) for k, v in subs.items()}
                )
            n = int(expr_val)
            result = n % divisor == 0
        else:
            return {"success": False, "error": "divisibility requires args"}
    else:
        return {"success": False, "error": f"Unknown number theory operation: {sub_op}"}

    return {"success": True, "result": str(result), "latex_result": str(result)}


# =============================================================================
# Combinatorics
# =============================================================================


def op_combinatorics(params: Doc) -> Doc:
    """Combinatorics: binomial, factorial, fibonacci, catalan, bell,
    permutations, combinations."""
    import sympy

    sub_op = params.get("sub_operation", "binomial")
    # Single-argument combinatorial numbers share this "n" fallback chain.
    n_arg: Any = params.get("n", params.get("expression", "0"))

    if sub_op == "binomial":
        n = int(n_arg)
        k = int(params.get("k", "0"))
        result = sympy.binomial(n, k)
    elif sub_op == "factorial":
        n = int(n_arg)
        result = sympy.factorial(n)
    elif sub_op == "fibonacci":
        n = int(n_arg)
        result = sympy.fibonacci(n)
    elif sub_op == "catalan":
        n = int(n_arg)
        result = sympy.catalan(n)
    elif sub_op == "bell":
        n = int(n_arg)
        result = sympy.bell(n)
    elif sub_op == "permutations":
        n = int(params["n"])
        k = int(params.get("k", str(n)))
        result = sympy.factorial(n) // sympy.factorial(n - k)
    elif sub_op == "combinations":
        n = int(params["n"])
        k = int(params["k"])
        result = sympy.binomial(n, k)
    else:
        return {"success": False, "error": f"Unknown combinatorics operation: {sub_op}"}

    return {"success": True, "result": str(result), "latex_result": str(result)}


# =============================================================================
# Geometry
# =============================================================================


def op_geometry(params: Doc) -> Doc:
    """Geometry computations using sympy.geometry."""
    import sympy
    from sympy.geometry import Circle, Line, Point, Polygon, Segment, Triangle

    sub_op = params.get("sub_operation", "distance")

    # Parse coordinate points if provided
    points_data = params.get("points", [])
    points = []
    for p in points_data:
        coords = [_parse_expr(str(c)) for c in p]
        points.append(Point(*coords))

    if sub_op == "distance":
        result = points[0].distance(points[1])

    elif sub_op == "midpoint":
        result = Segment(points[0], points[1]).midpoint

    elif sub_op == "slope":
        result = Line(points[0], points[1]).slope

    elif sub_op == "line_equation":
        x, y = sympy.symbols("x y")
        result = Line(points[0], points[1]).equation(x, y)

    elif sub_op == "area":
        if params.get("sides"):
            # Heron's formula from side lengths
            sides = [_parse_expr(str(s)) for s in params["sides"]]
            a, b, c = sides
            s = (a + b + c) / 2
            result = sympy.sqrt(s * (s - a) * (s - b) * (s - c))
        elif params.get("radius") is not None:
            radius = _parse_expr(str(params["radius"]))
            result = sympy.pi * radius**2
        elif len(points) == 3:
            result = Triangle(*points).area
        elif len(points) > 3:
            result = Polygon(*points).area
        else:
            return {"success": False, "error": "area requires points, sides, or radius"}

    elif sub_op == "perimeter":
        if params.get("sides"):
            sides = [_parse_expr(str(s)) for s in params["sides"]]
            result = sum(sides)
        elif params.get("radius") is not None:
            radius = _parse_expr(str(params["radius"]))
            result = 2 * sympy.pi * radius
        elif len(points) == 3:
            result = Triangle(*points).perimeter
        elif len(points) > 3:
            result = Polygon(*points).perimeter
        else:
            return {"success": False, "error": "perimeter requires points, sides, or radius"}

    elif sub_op == "angle":
        if len(points) == 3:
            # Angle at vertex points[1]
            t = Triangle(*points)
            result = t.angles[points[1]]
        elif len(points) == 4:
            l1 = Line(points[0], points[1])
            l2 = Line(points[2], points[3])
            result = l1.angle_between(l2)
        else:
            return {
                "success": False,
                "error": "angle requires 3 points (vertex) or 4 points (two lines)",
            }

    elif sub_op == "centroid":
        result = Triangle(*points).centroid

    elif sub_op == "circumcenter":
        result = Triangle(*points).circumcenter

    elif sub_op == "circumradius":
        result = Triangle(*points).circumradius

    elif sub_op == "incenter":
        result = Triangle(*points).incenter

    elif sub_op == "inradius":
        result = Triangle(*points).inradius

    elif sub_op == "orthocenter":
        result = Triangle(*points).orthocenter

    elif sub_op == "is_collinear":
        result = Point.is_collinear(*points)

    elif sub_op == "intersection":
        if params.get("radius") is not None and len(points) >= 3:
            # Line-circle intersection
            line = Line(points[0], points[1])
            radius = _parse_expr(str(params["radius"]))
            circle = Circle(points[2], radius)
            result = line.intersection(circle)
        elif len(points) == 4:
            l1 = Line(points[0], points[1])
            l2 = Line(points[2], points[3])
            result = l1.intersection(l2)
        else:
            return {
                "success": False,
                "error": "intersection requires 4 points or 2 points + center + radius",
            }

    elif sub_op == "tangent_lines":
        radius = _parse_expr(str(params["radius"]))
        circle = Circle(points[0], radius)
        result = circle.tangent_lines(points[1])

    elif sub_op == "arc_length":
        radius = _parse_expr(str(params["radius"]))
        angle = _parse_expr(str(params.get("angle", "2*pi")))
        result = radius * angle

    elif sub_op == "sector_area":
        radius = _parse_expr(str(params["radius"]))
        angle = _parse_expr(str(params.get("angle", "2*pi")))
        result = sympy.Rational(1, 2) * radius**2 * angle

    elif sub_op == "regular_polygon_area":
        n_sides = int(params["n_sides"])
        side_length = _parse_expr(str(params["side_length"]))
        # A = (n * s^2) / (4 * tan(pi/n))
        result = sympy.Rational(n_sides, 4) * side_length**2 / sympy.tan(sympy.pi / n_sides)

    else:
        return {"success": False, "error": f"Unknown geometry operation: {sub_op}"}

    # Format result
    if isinstance(result, list | tuple):
        result_str = ", ".join(str(r) for r in result)
        latex_str = result_str
    elif isinstance(result, Point):
        result_str = f"({result.x}, {result.y})"
        latex_str = result_str
    elif isinstance(result, bool):
        result_str = str(result)
        latex_str = result_str
    else:
        result_str = str(result)
        latex_str = _to_latex(result)

    return {"success": True, "result": result_str, "latex_result": latex_str}


# =============================================================================
# Differential equations
# =============================================================================


def op_dsolve(params: Doc) -> Doc:
    """Solve an ordinary differential equation."""
    import sympy

    var = sympy.Symbol(params.get("variable", "x"))
    func_name = params.get("function", "y")
    f = sympy.Function(func_name)

    eq_str: Any = params.get("equation", params.get("expression", ""))

    # Namespace for parsing ODE expressions with Derivative notation
    namespace = {
        str(var): var,
        func_name: f,
        "Derivative": sympy.Derivative,
        "pi": sympy.pi,
        "E": sympy.E,
        "I": sympy.I,
        "oo": sympy.oo,
        "sqrt": sympy.sqrt,
        "sin": sympy.sin,
        "cos": sympy.cos,
        "tan": sympy.tan,
        "exp": sympy.exp,
        "log": sympy.log,
        "Rational": sympy.Rational,
    }

    if "=" in eq_str and "==" not in eq_str:
        parts = eq_str.split("=", 1)
        lhs = sympy.sympify(parts[0].strip(), locals=namespace)
        rhs = sympy.sympify(parts[1].strip(), locals=namespace)
        eq = sympy.Eq(lhs, rhs)
    else:
        parsed = sympy.sympify(eq_str, locals=namespace)
        eq = sympy.Eq(parsed, 0)

    result = sympy.dsolve(eq, f(var))

    return {"success": True, "result": str(result), "latex_result": _to_latex(result)}


# =============================================================================
# Chain (multi-step with result piping)
# =============================================================================


def _resolve_references(obj: Any, namespace: dict[str, Any]) -> Any:
    """Recursively substitute $name and $name[idx] references in string values."""
    import re

    if isinstance(obj, str):

        def replace_ref(match: re.Match[str]) -> str:
            name = match.group(1)
            idx_str = match.group(2)
            if name not in namespace:
                return match.group(0)  # Leave unresolved
            val = namespace[name]
            if idx_str is not None:
                idx = int(idx_str)
                if isinstance(val, list | tuple):
                    val = val[idx]
                else:
                    return match.group(0)
            # Wrap in parens to preserve operator precedence
            s = str(val)
            if any(token in s for token in ["+", "-", "/", " "]):
                return f"({s})"
            return s

        # Match $name[idx] or $name (word boundary)
        return re.sub(r"\$(\w+)(?:\[(\d+)\])?", replace_ref, obj)
    elif isinstance(obj, dict):
        return {
            k: _resolve_references(v, namespace) if k not in ("operation", "result_name") else v
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [_resolve_references(item, namespace) for item in obj]
    return obj


def _parse_result_to_sympy(raw: str) -> Any:
    """Convert a SymPy result string back to a SymPy object or list of objects."""
    import ast

    import sympy

    raw = raw.strip()

    # Handle list results like "['2', '-3']" or "[-3, -2]"
    if raw.startswith("[") and raw.endswith("]"):
        try:
            items = ast.literal_eval(raw)
            parsed = [sympy.sympify(str(item)) for item in items]
            # Unwrap single-element lists for easier reference
            if len(parsed) == 1:
                return parsed[0]
            return parsed
        except Exception:
            pass

    # Handle dict results like "{x: 2, y: 3}"
    if raw.startswith("{") and raw.endswith("}"):
        try:
            # SymPy dict format: {x: 2, y: 3}
            return sympy.sympify(raw)
        except Exception:
            pass

    # Handle boolean
    if raw in ("True", "False"):
        return raw == "True"

    # Handle point tuples like "(2, 3)"
    if raw.startswith("(") and raw.endswith(")"):
        try:
            items = ast.literal_eval(raw)
            if isinstance(items, tuple):
                return tuple(sympy.sympify(str(item)) for item in items)
        except Exception:
            pass

    # Standard expression
    try:
        return _parse_expr(raw)
    except Exception:
        try:
            return sympy.sympify(raw)
        except Exception:
            return raw  # Return as string if all parsing fails


def op_chain(params: Doc) -> Doc:
    """Execute a chain of operations with result piping via a namespace."""
    import sympy

    steps = params.get("steps", [])
    if not steps:
        return {"success": False, "error": "No steps provided"}

    namespace: dict[str, Any] = {}  # result_name -> SymPy expression or value
    step_results: list[dict[str, Any]] = []

    # Build a SymPy-aware namespace for combine expressions
    sympy_ns = {
        "pi": sympy.pi,
        "E": sympy.E,
        "I": sympy.I,
        "oo": sympy.oo,
        "sqrt": sympy.sqrt,
        "sin": sympy.sin,
        "cos": sympy.cos,
        "tan": sympy.tan,
        "exp": sympy.exp,
        "log": sympy.log,
        "Rational": sympy.Rational,
        "Abs": sympy.Abs,
        "simplify": sympy.simplify,
        "factor": sympy.factor,
        "expand": sympy.expand,
    }

    for i, step in enumerate(steps):
        step_op = step.get("operation", "")
        result_name = step.get("result_name", f"step_{i}")

        # Resolve $references in step parameters
        resolved = _resolve_references(step, namespace)

        if step_op == "combine":
            # Evaluate a combining expression with full namespace
            expr_str = resolved.get("expression", "")
            local_ns = {**sympy_ns, **namespace}
            try:
                result = sympy.sympify(expr_str, locals=local_ns)
                result = sympy.simplify(result)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Step {i} (combine) failed: {e}",
                    "step_results": step_results,
                }
            step_result: Doc = {
                "success": True,
                "result": str(result),
                "latex_result": _to_latex(result),
            }
        elif step_op in _OPERATIONS:
            step_result = _OPERATIONS[step_op](resolved)
        else:
            return {
                "success": False,
                "error": f"Step {i}: unknown operation '{step_op}'",
                "step_results": step_results,
            }

        if not step_result.get("success"):
            return {
                "success": False,
                "error": f"Step {i} ({step_op}) failed: {step_result.get('error', '')}",
                "step_results": step_results,
            }

        # Store result in namespace for later steps
        namespace[result_name] = _parse_result_to_sympy(step_result["result"])
        step_results.append(
            {
                "step": i,
                "operation": step_op,
                "result_name": result_name,
                "result": step_result["result"],
                "latex_result": step_result.get("latex_result", ""),
            }
        )

    # Return the final step's result
    final = step_results[-1] if step_results else {"result": "", "latex_result": ""}
    return {
        "success": True,
        "result": final["result"],
        "latex_result": final.get("latex_result", ""),
        "step_results": step_results,
    }


_OPERATIONS = {
    "solve": op_solve,
    "simplify": op_simplify,
    "verify": op_verify,
    "factor": op_factor,
    "expand": op_expand,
    "evaluate": op_evaluate,
    "latex": op_latex,
    "integrate": op_integrate,
    "diff": op_diff,
    "limit": op_limit,
    "series": op_series,
    "summation": op_summation,
    "product": op_product,
    "trigsimp": op_trigsimp,
    "apart": op_apart,
    "matrix": op_matrix,
    "number_theory": op_number_theory,
    "combinatorics": op_combinatorics,
    "dsolve": op_dsolve,
    "geometry": op_geometry,
    "chain": op_chain,
}


class _Timeout(Exception):
    pass


def run(operation: str, params: Doc, timeout: int = DEFAULT_TIMEOUT) -> Doc:
    """Execute an operation with a SIGALRM timeout, returning a
    ``{success, error}`` envelope on any failure (never raises)."""
    handler = _OPERATIONS.get(operation)
    if handler is None:
        return {
            "success": False,
            "error": f"Unknown operation: {operation}. Valid: {', '.join(_OPERATIONS)}",
        }

    timeout = max(1, min(int(timeout), 120))
    previous = signal.getsignal(signal.SIGALRM)

    def _handle(_signum: int, _frame: Any) -> None:
        raise _Timeout

    try:
        signal.signal(signal.SIGALRM, _handle)
        signal.alarm(timeout)
        try:
            return handler(params)
        except _Timeout:
            return {"success": False, "error": f"Operation timed out ({timeout}s limit)"}
        except Exception as exc:  # noqa: BLE001 — wrap failures in the error envelope
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
